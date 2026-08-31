# ki-icl

The company context layer: facts about KI group, fetchable by id through an MCP client.

**This is a proof of concept.**
It implements the read path from `context-layer-phase1-spec.md` end to end, minus Azure.
It is not production ready, and the content in `domains/` is placeholder text that no owner has reviewed.

## What it is for

Skills tell an assistant *how to perform a task*.
This tells it *what is true about the company*: methodologies, offerings, guidelines, policy, and, since the `projects` domain landed, the current state of the work.

One sentence: **make a markdown file in a git repository fetchable through the KI MCP, by id, with a version stamp.**

The property that matters is that a wrong answer is impossible.
A lookup is an exact dictionary hit or an honest `not_found`.
There is no similarity path, no closest match, and no fuzzy-matching code anywhere to be reached.

Holding current state alongside stable documents adds a second way to be wrong, and it is worth naming.
A governed document is wrong only if it was written wrong; a status is wrong the moment it is out of date, and it looks identical either way.
`version_id` cannot help, because it is opaque by design and detects change rather than age.
That is why every file reporting a point in time carries an `**As of YYYY-MM-DD**` line, why the gate rejects one that does not, and why the date is lifted into the manifest so staleness is computable without opening anything.

## Domains

Eight, and the list is a decision rather than a convention.
The six business functions come from the project lead's model.
`company` and `projects` are additions, and each is justified below the table.

| Domain | The function | Look here for |
|---|---|---|
| `company` | (addition) | Who KI group is: what it does, how it positions itself, the certifications and regulatory commitments it operates under |
| `projects` | (addition) | What we are working on right now, one artifact per engagement: goal, scope, stage, health, team, decisions, dates |
| `value-creation` | Value Creation | Engineering work: methodologies, technical standards, review practices |
| `value-delivery` | Value Delivery | After-sales communication to the customer: handover, escalation, and the conventions for what a customer is told. Never the current state of an engagement, which is `projects` |
| `marketing` | Marketing | Creating awareness: positioning, messaging, content, events, campaigns |
| `sales` | Sales | Offers, estimation and man day rates, pricing, contract shape |
| `finance` | Finance | Budgets, invoicing, cost and expense rules, approval thresholds, reporting |
| `hr` | HR | People and legal: employment, personnel processes, and the contracts around them |

`company` exists because identity facts belong to no single function, and the old catch-all `hr` description was evidence somebody already needed that home.

`projects` is the one domain that is **not** a business function, and it is worth being explicit about why.
Every other domain holds facts that are stable and reusable: how we run a discovery workshop, what we reimburse.
A project is the opposite, per-instance state that changes weekly, and the routing rule below does not separate the two because engineering owns both.
The justification is volatility and cardinality rather than function: putting weekly-changing project state beside the methodology that should be stable would make one domain do two jobs, and projects accumulate without bound while functions do not.

Five of the eight hold no artifacts yet, and that is the intended state rather than an unfinished one.
An empty domain answers `get_domain_manifest` with an honest empty list, and it gives `report_gap` somewhere correct to put the demand.
That tool asks an agent to pick a domain "from `list_domains`", so a marketing question with no `marketing` domain to name would land under a mislabelled one or vanish entirely.

`KNOWN_DOMAINS` in [scripts/validate_context.py](scripts/validate_context.py) is the gate.
Adding a domain means amending that constant and saying why in the pull request, because a new domain changes how the whole corpus is organised and every telemetry key written against it.

### Which domain does an artifact go in

**The one whose team authors and maintains it, not the one whose people are most likely to ask.**

Routing by owner is the only rule that agrees with the axis ownership will land on.
`owner` is already reserved at both levels and served as `null`, so `CODEOWNERS` and approval routing will key off exactly this, and routing by audience would put the two in permanent disagreement.
Audiences overlap and drift anyway.
Retrieval from the other side is unaffected, because the artifact's own `description` carries the "when to use" triggers wherever the artifact sits.

Two artifacts in the repo are worked examples, and both land against what the audience rule would have said:

| Artifact | Domain, and why | The audience rule would have said |
|---|---|---|
| `expense-policy` | `finance`, which sets the thresholds and the evidence rules | `hr`, since an employee is the one asking |
| `discovery-workshop` | `value-creation`, which owns the methodology | `sales`, since its own description says "when preparing, scoping, or quoting" |

The rule also decides where the project reporting convention lives.
`project-status-reporting` is in `value-creation`, not `projects`, because engineering authors and maintains it, and because `projects` holds engagements rather than documents about engagements.

### The `projects` domain

One folder per engagement, named as the business names it, so `list_domains` then `get_domain_manifest("projects")` is enough to find the one being asked about.
Every project uses the same file layout, and that uniformity is the point: it is what lets a question be answered from the right file without anyone having learned that project's particular habits.

| File | Answers | Required |
|---|---|---|
| `README.md` | What are we doing, what is in and out of scope | yes |
| `status.md` | What stage, what health, what moved, what is blocked | yes |
| `team.md` | Who is working on it, and who decides what on the customer side | yes |
| `decisions.md` | What was decided and why, and what it cost | when there is one |
| `timeline.md` | What is due when, and what has slipped | when dates are committed |

`REQUIRED_ARTIFACT_FILES` in [scripts/validate_context.py](scripts/validate_context.py) enforces the required three, as data rather than a per-domain branch so the next domain needing a shape is a dictionary entry.
`decisions.md` and `timeline.md` are deliberately not required: a project in discovery has settled no arguments and committed to no dates, and empty files would be worse than absent ones.

### Listing projects, and why there is no `list_projects` tool

`get_domain_manifest("projects")` **is** the listing tool.
A per-domain listing tool would duplicate it and cost a property worth keeping: `list_domains` is the single cold-start entry point, adding a domain touches no tool signature, and once `list_projects` exists the next question is why not `list_policies`.

What the listing did lack was status.
The manifest carries id, title, kind and description, while stage and health live inside `status.md`, so "which projects are at risk" would have meant fetching every project in full and reading prose.

So the packager lifts the `status.md` header into the manifest as a `progress` object, exactly as it already derives `version_id` by running git at package time:

```json
{ "id": "dhl-cbs", "title": "DHL CBS",
  "progress": { "as_of": "2026-08-28", "stage": "delivery", "health": "at risk" } }
```

Derived rather than duplicated into `artifact.yaml`, because two copies of a fact drift and `status.md` is the one an engineer actually edits.
One call now answers which projects are off track, what sits in each stage, and whose status has gone stale.
`progress` is present only on artifacts that have a `status.md`, so its presence is the check for "does this report progress" and no other domain carries dead keys.
[scripts/status_header.py](scripts/status_header.py) defines the format once, because the gate and the packager reading it separately would drift.

When the portfolio grows this manifest is what grows with it, since it is O(projects).
The pressure valve is `closed` and `stopped` projects, which stay readable but should eventually move out of the live listing rather than the tool gaining a filter argument.

**Every status, team and timeline file must carry an `**As of YYYY-MM-DD**` line, and the validator fails without it.**
This is the one rule in the domain that is about correctness rather than tidiness.
`version_id` is opaque by design, compared for equality and never parsed, so it cannot tell an agent that a status is three months old.
Without a date in the content a stale status answers confidently and nobody can tell, which is precisely the wrong answer this repo exists to make impossible.
An agent answering from this domain is expected to say the date: not "the project is at risk" but "as of 28 August it was at risk".

`**Stage:**` and `**Health:**` are gated the same way, against closed vocabularies.
Free text is what defeats the reason those fields exist: `in progress`, `ongoing` and `phase 2` are all answers somebody would write for stage, and none of them compares to anything.
A value outside the vocabulary is dropped rather than guessed, so it fails the gate instead of reaching the manifest as a plausible-looking null.

Note the fetch granularity.
`get_artifact` returns every file in the folder in one call, so the file split serves human editing and precise quoting, not fetch size.
A project folder with six files returns all six every time, which is the reason to keep each one tight.

The conventions, the stage and health vocabularies, and what an update is meant to cost are in [project-status-reporting](domains/value-creation/project-status-reporting/README.md).
Two things there are unresolved and matter before real project data lands: read access is broad by construction, so customer names and slipped commitments would be readable by anyone reaching the server, and nothing yet fails when a status goes stale.

Domain ids are close to permanent.
`version_id` comes from the last commit touching `domains/<domain>/<artifact>/`, so renaming a domain restamps every artifact inside it at once.
Usage records are keyed on domain plus id, so a rename also splits an artifact's history into two unrelated series.

## Quick start

```bash
make install     # .venv + dependencies
make test        # 165 tests
make demo        # walk the acceptance demo end to end
make serve-http  # MCP server on http://127.0.0.1:8000/mcp
make inspector   # serve, and open MCP Inspector against it
make usage       # what was looked up, and what was asked for and missed
```

## Layout

```
domains/<domain>/                   the content. One folder per domain, one per artifact.
  domain.yaml                       id + description
  <artifact-id>/                    an artifact; the folder IS the artifact
    artifact.yaml                   title, kind, description
    README.md                       required entry document
scripts/validate_context.py         the gate. Collects every failure, never stops at the first.
scripts/package_context.py          tar.gz + per-domain _manifest.json + per-artifact version_id
scripts/status_header.py            the status.md header format. Defined once; the gate and
                                    the packager both read it, so they cannot drift.
scripts/demo.py                     the acceptance demo, over a real MCP client
scripts/usage_report.py             reads logs/usage.jsonl
server/artifacts.py                 the read path. Lifts into ki-mcp unchanged.
server/usage.py                     usage logging middleware. Lifts into ki-mcp.
server/mcp_server.py                throwaway harness. Replaced by ki-mcp's tool registry.
```

`make package` writes two directories, and the difference matters:

| Directory | Contents | Used by |
|---|---|---|
| `dist/context/` | exactly `context.tar.gz` and `manifest.json` | the upload step (`az storage blob upload-batch`) |
| `dist/staging/` | the servable tree, byte-identical to the extracted archive | local dev (`CONTEXT_ROOT`) |

## Adding an artifact

1. Pick the domain whose team will own the document, per the routing rule above, then `mkdir domains/<domain>/<kebab-case-id>/`.
2. Write `artifact.yaml` with `title`, `kind`, `description` and `review`.
   The `description` is what an agent reads to decide whether to fetch, so write it as a "when to use" signal, not a label.
   `review` is one of `demo`, `draft` or `approved`, and is required. See below.
3. Write `README.md`. Supporting files may nest freely, and must be text.
4. `make validate`, then open a pull request.

Text only: `.md`, `.yaml`, `.yml`, `.json`, `.txt`, `.csv`, 1 MiB per file.
This is not a style preference.
Measured on the existing skills catalog, 147 markdown files are 1.73 MB while the binaries beside them are 15.32 MB.
Repository size risk is entirely a binaries risk, and the allow-list removes it.

## `review`: whether anyone stands behind it

Every artifact declares one of three states, and the validator rejects anything else:

| `review` | Means |
|---|---|
| `demo` | Invented content that exists to exercise the pipeline. Must never be repeated as fact about the company. |
| `draft` | Real subject, written but not reviewed by an owner. |
| `approved` | An owner has reviewed it and stands behind it. |

Nothing here is `approved` yet.

Required rather than optional, and the reason is a failure that was observed rather than imagined.
This README has always opened by saying the content is unreviewed placeholder text, and **no caller of the API could ever see that**.
A manifest row reads as settled fact whatever its provenance, and since the row is now the whole answer to a portfolio question, an agent asked "which projects are not on track" would answer from invented data with nothing anywhere to contradict it.
The disclaimers sat in the file bodies, which the recommended path never opens.

So the state is served on the row, and `get_domain_manifest` appends a caveat naming the unapproved artifacts:

```
Fetch with `get_artifact("projects", ["<id>"])`. NOT APPROVED: `demo` (dhl-cbs,
nordwind-dispatch). Say so in any answer drawn from these, and never present
`demo` content as fact.
```

The caveat travels with the row that needs it, because an instruction an agent read once at connection time loses to a payload that looks like fact.
Leaving the field optional would have restored the hole for every artifact that omitted it, which is why it is required instead.

## The three tools

| Tool | Returns |
|---|---|
| `list_domains()` | one row per domain, forever. The cold-start entry point. |
| `get_domain_manifest(domain)` | one row per artifact, with descriptions to choose from. No file bodies. Carries `progress` for artifacts that report it, which is what makes it the project listing. |
| `get_artifact(domain, ids)` | full text. Accepts one id or a list; each is answered independently. |
| `report_gap(domain, topic)` | records that the manifest had no answer, so the gap can be written up. |

### Why `report_gap` exists

Tested against real agents, the read path alone loses the signal it most needs.

Ask an agent about something the corpus does not cover and it does the right thing: reads the manifest, sees nothing matching, and says so.
It never calls `get_artifact`, so **nothing is recorded**, and the demand is invisible.
The better the agent behaves, the less the system learns.

`report_gap` gives it somewhere to put the finding.
The `topic` is a constrained kebab-case label, never the user's question, so the privacy position is unchanged: a topic label is not free text and cannot carry a sentence.
Overlong or non-conforming topics are rejected with an explanation rather than silently normalised into nonsense.

The dashboard shows both signals and distinguishes them, because they are not equal evidence:

- **reported** &mdash; an agent read the manifest, found no answer, and said so. Deliberate, and the only signal that survives a well-behaved agent.
- **guessed** &mdash; an agent asked for an id that does not exist. Incidental, but it is also what catches a stale client asking for something that was deleted.

Suggestions arrive filtered only by the agent's judgement, so every row carries three actions.
They differ in what happens when more demand arrives afterwards, which is the whole reason there are three:

| Action | Meaning | If it is asked for again |
|---|---|---|
| **resolved** | written up | comes back, flagged in red. The document exists and people are still missing it, so it is not reachable and something is broken |
| **dismiss** | not now | comes back. A dismissal judges the demand so far, and more demand is new information |
| **delete** | never | nothing to come back to. Asks for confirmation, then erases the records that produced the suggestion. If somebody asks again it returns as a new row, starting from one |

Dismiss and resolve are marks, which are filters over the log.
Delete deliberately is not, and that is the one interesting decision in this panel.

A mark that has to hold forever is a tombstone: it accumulates, nothing on the page shows it, and it silently swallows the next person who asks for the same thing.
That is not hypothetical.
It cost a real test cycle here: a row was deleted while trying the button out, an agent reported that exact gap thirty seconds later, and the dashboard showed nothing with no way to find out why.

So delete goes at the source instead.
`purge` removes the demand records for that key, clears any mark it had, and appends one `purge` record saying what it removed, because the log stops being append-only at that moment and has to be able to explain its own counts.
Nothing is left to remember, so nothing can be silently suppressed.
The cost, stated in the confirmation dialog: those lookups also leave the counters, and there is no undo.

Each mark records the demand it was made at, which is what makes it revisitable rather than a permanent mute.
Re-marking raises the baseline, so "seen it, still not writing it" holds until the next time somebody asks.
A row also shows **now written** when the id appears in the current catalog, so marking something resolved is a claim the catalog can corroborate.

Curation lives in `logs/curation.json`, deliberately apart from the usage log: the log records what happened, curation records what you decided, and in production those belong in different places.
`purge` is the single exception that touches both, which is why it is a separate function and a separate endpoint rather than a fourth state.

Every answer carries an opaque `version_id`, taken from the last commit that touched that artifact's folder.
Compare it for equality to detect staleness.
Never parse it.

## Usage logging

Every context lookup is recorded, one line per artifact actually looked up:

```json
{"ts":"2026-08-28T09:04:11Z","event":"context_use","tool":"get_artifact",
 "domain":"finance","id":"expense-policy","outcome":"found",
 "version_id":"b0f9dd0a...","file_count":2,"duration_ms":0.7}
{"ts":"2026-08-28T09:04:11Z","event":"context_use","tool":"get_artifact",
 "domain":"hr","id":"parental-leave","outcome":"not_found","duration_ms":0.4}
```

**The miss records are the reason this exists.**
An id that is asked for repeatedly and never found is a document somebody needs and nobody has written, and nothing else in the system carries that signal.
`make usage` puts them in their own table.

Two properties worth keeping:

- **No tool knows about it.** It is FastMCP middleware, so adding a tool needs no logging code and no allowlist entry, and logging cannot fall out of step with the tool list. A logging failure is swallowed: it must stay an annoyance, never an outage.
- **No caller identity is recorded.** The log says what was looked up, never who looked it up, so no personal data is processed and no Art. 6 GDPR basis is needed. If that ever has to change, port `ki-mcp`'s `utils/observability._caller()` rather than writing a second one: it emits a keyed digest gated on a configured salt, and never an email or a raw object id. Note that a pseudonym is still personal data under GDPR, so that step needs a documented basis.

Sinks are stderr plus `logs/usage.jsonl`. Set `CONTEXT_USAGE_LOG=""` to leave stderr as the only one, which is what production wants: stdout is already collected by Log Analytics and a file would be a second store to own.

### The dashboard

`make dashboard` serves [scripts/dashboard.py](scripts/dashboard.py) on `:8010`.
It reads the log file directly, so it needs no MCP server running, and it re-polls every three seconds so records appear while you test.

All aggregation is pure Python functions over a list of records, covered by tests; [server/dashboard.html](server/dashboard.html) only renders what it is handed.

| Panel | The question it answers |
|---|---|
| What to write next | Which ids were asked for and not found, ranked by demand, and whether each ever existed |
| Artifacts served | What people actually read, and the bytes it cost |
| Discovery funnel | Of the sessions that browsed, how many could choose something. Sessions that already knew an id are counted separately as `direct` |
| Chosen against offered | Which descriptions win when an agent has to pick |
| Versions served | Which versions went out, flagged when an artifact changed mid-window |
| Latency, size against time | Whether payload size is what costs time |

Three decisions in there worth not undoing:

- **The funnel is conditional.** Each step counts only sessions that reached the previous one. Counting them independently would let a session that already knew an id mask the drop-off the panel exists to show.
- **Errors are excluded from latency.** A call that raised is not a measurement of service time, and one slow failure drags p95 far enough to flatten every other bar.
- **Percentiles are nearest-rank.** `statistics.quantiles` defaults to the exclusive method, which extrapolates past the data: a six-sample p95 came out at 25.7 ms when the slowest call measured was 3.7 ms.

## Connecting a client

```bash
make inspector
```

Starts the server and opens MCP Inspector against [mcp-inspector.json](mcp-inspector.json), which lists this repo's two entries and nothing else.
Both serve the same three tools; `ki-icl-http` is the one `make inspector` starts.

Inspector's default ports collide with any other Inspector already running, and it fails rather than falling back, so override them:

```bash
make inspector INSPECTOR_CLIENT_PORT=6474 INSPECTOR_SERVER_PORT=6477
```

To run the server alone, `make serve-http`, then point a client at `http://127.0.0.1:8000/mcp`. For stdio from another tool:

```json
{
  "mcpServers": {
    "ki-icl": {
      "command": "/absolute/path/to/ki-icl/.venv/bin/python",
      "args": ["/absolute/path/to/ki-icl/server/mcp_server.py"],
      "env": { "CONTEXT_ROOT": "/absolute/path/to/ki-icl/dist/staging" }
    }
  }
}
```

## What this POC leaves out

Everything here is deliberate, and each item is cheap to add once it is wanted.

| Not built | Why, and what it costs |
|---|---|
| Azure blob fetch, ETag guard, cache swap | Parametrizing `ki-mcp/server/utils/skills_source.py`, which already works in production. Nothing new to design. |
| `publish-context.yml` (upload + `/refresh`) | Blocked on a federated credential for this repo on the publisher Entra app. Copy `ki-dev-skills/.github/workflows/publish-skills.yml` and change four things. |
| Registration in ki-mcp | Move `server/artifacts.py` to `ki-mcp/server/utils/artifacts.py` and the three tool functions to `server/tools/artifact_tools.py`. |
| Ownership, `CODEOWNERS`, approval routing | Deferred by decision. `owner` is already served as `null` at both levels, so adding it is data, not a schema change. The routing rule above is chosen to agree with it when it lands. |
| A fixed `kind` vocabulary | Issue #20 OQ-3, undecided. `kind` is a non-empty free string, and two values are in use: `guideline` and `methodology`. Seven domains will pull it in more directions, so it is worth settling soon, but generalising before there is content to generalise from would be the wrong order. |
| Search, similarity, resolution from task context | Deferred on a stated trigger. Adding it would break the property in the first section. |
| Aggregating usage beyond a local file | `logs/usage.jsonl` is POC scaffolding. In ki-mcp the records go to stderr and Log Analytics collects them, so the file sink is deleted, not ported. |
| Binary assets | See the text-only rule above. |

## Lifting the read path into ki-mcp

`server/artifacts.py` is written against the spec's section 7.3 signatures so it moves unchanged apart from two lines:

- `ARTIFACTS_ROOT` becomes `settings.artifacts_dir`.
- `_file_payload` is deleted in favour of `from utils.file_payload import file_payload`.

`visible_domains()` is a pass-through today and must stay the only way a read path resolves a domain.
It is the seam per-identity scoping lands on, and its whole value is that no read path can be written that forgets it.
