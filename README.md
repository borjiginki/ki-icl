# ki-icl

The company context layer: facts about KI group, fetchable by id through an MCP client.

**This is a proof of concept.**
It implements the read path from `context-layer-phase1-spec.md` end to end, minus Azure.
It is not production ready.

**The corpus itself lives in a separate repository, [ki-ccl](https://github.com/ki-group-gmbh/ki-ccl).**
This repo is the server and its deployment: the read path, authorization, and the Azure infrastructure that serves `ki-ccl`'s content over MCP.
Everything below that used to describe editing `domains/` directly now describes `ki-ccl`; see its README for the corpus itself, and [Publishing](#publishing) below for how it reaches this server.

## What it is for

Skills tell an assistant *how to perform a task*.
This tells it *what is true about the company*: methodologies, offerings, guidelines, policy, and, since the `projects` domain landed, the current state of the work.

One sentence: **make a markdown file in a git repository fetchable through the KI MCP, by id, with a version stamp.**

Governed artifacts may be authored manually or generated from a software repository
with `ki-ccl`'s `generate-project-context` skill. Generation uses the read-only inspector
(`.venv/bin/python skills/generate-project-context/scripts/inspect_project.py /absolute/path/to/project --pretty`,
run from within `ki-ccl`), asks a human about consequential facts the repository cannot
prove, and requires review before writing under `domains/projects/<project-id>/`. It
does not execute target code or collect/reproduce secrets. Run `make validate && make
package` in `ki-ccl` after review. The existing exact-lookup guarantee remains
unchanged.

The property that matters is that a wrong answer is impossible.
A lookup is an exact dictionary hit or an honest `not_found`.
There is no similarity path, no closest match, and no fuzzy-matching code anywhere to be reached.

Holding current state alongside stable documents adds a second way to be wrong, and it is worth naming.
A governed document is wrong only if it was written wrong; a status is wrong the moment it is out of date, and it looks identical either way.
`version_id` cannot help, because it is opaque by design and detects change rather than age.
That is why every file reporting a point in time carries an `**As of YYYY-MM-DD**` line, why the gate rejects one that does not, and why the date is lifted into the manifest so staleness is computable without opening anything.

## Domains

Seven, and the list is a decision rather than a convention.
The original partition followed a value-chain-plus-support-function model with eight business-function domains.
It was replaced on 2026-09-09, when real content first landed and turned out not to fit that shape: several of the eight had no content in sight, while a 119-case reference library and 13 personal staffing profiles had nowhere to go without overloading `sales` and `hr` or fragmenting across several domains.
The domains below instead follow what an agent is actually trying to do, mirroring the organizing principle of the source layer this content was drawn from.

| Domain | Look here for |
|---|---|
| `company` | Who KI group is: the ecosystem of companies, organisational structure, mission, positioning and locations |
| `method` | How KI group thinks about and runs its work: the strategic operating canon and the methodologies behind how an engagement is scoped, built and reported on |
| `offerings` | What KI group sells and how to talk about it: the offering catalogue, the delivery arc and gates, positioning and proof |
| `case-studies` | **Closed, delivered** reference engagements KI group can point to, one artifact per case, cleared for naming the client - "have we built this before" |
| `team` | Who is on the KI group team and what they can do: role, skills, experience and certifications, for staffing work |
| `marketing` | How KI group presents its brand: corporate identity, visual rules, and, as it grows, tone of voice and templates |
| `projects` | What KI group is working on **right now**, one artifact per **live** engagement: goal, scope, stage, health, team, decisions, dates - "what's happening today" |

`case-studies` and `projects` are easy to conflate, because both are "an engagement" - the test is time, not topic. An engagement moves from `projects` to `case-studies` exactly once, on delivery, never both at once. See `ki-ccl`'s README for the fuller breakdown and why `projects` currently ships empty.

`company` and `projects` keep their original justification unchanged: identity facts belong to no single function, and `projects` is per-instance state that changes weekly rather than the stable, reusable material every other domain holds — putting the two side by side would make one domain do two jobs, and projects accumulate without bound while the rest do not.

Two of the seven, `company` and `projects`, hold no artifacts yet, and that is the intended state rather than an unfinished one.
An empty domain answers `get_domain_manifest` with an honest empty list, and it gives `report_gap` somewhere correct to put the demand.
That tool asks an agent to pick a domain "from `list_domains`", so a live-engagement question with no `projects` domain to name would land under a mislabelled one or vanish entirely.

`KNOWN_DOMAINS` in `ki-ccl`'s [scripts/validate_context.py](https://github.com/ki-group-gmbh/ki-ccl/blob/main/scripts/validate_context.py) is the gate.
Adding a domain means amending that constant (and the matching grants in `ki-ccl`'s [access-policy.yaml](https://github.com/ki-group-gmbh/ki-ccl/blob/main/access-policy.yaml)) and saying why in the pull request, because a new domain changes how the whole corpus is organised and every telemetry key written against it.
`SENSITIVITY_LEVELS` is defined twice by necessity, once here in [server/access.py](server/access.py) and once as a hand-kept mirror in `ki-ccl`'s gate — see the comment at its definition in either file for why.

### Which domain does an artifact go in

**The one whose team authors and maintains it, not the one whose people are most likely to ask.**

Routing by owner is the only rule that agrees with the axis ownership will land on.
`owner` is already reserved at both levels and served as `null`, so `CODEOWNERS` and approval routing will key off exactly this, and routing by audience would put the two in permanent disagreement.
Audiences overlap and drift anyway.
Retrieval from the other side is unaffected, because the artifact's own `description` carries the "when to use" triggers wherever the artifact sits.

One artifact in the repo is a worked example, and it lands against what the audience rule would have said:

| Artifact | Domain, and why | The audience rule would have said |
|---|---|---|
| `project-status-reporting` | `method`, which owns how a project artifact is laid out and maintains the convention | `projects`, since that is who asks about a project's layout |

The rule decides this one precisely because `projects` holds engagements rather than documents about engagements: the convention is authored and maintained by whoever owns delivery methodology, so it lives in `method` even though most questions about it will come from someone looking at `projects`.

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

`REQUIRED_ARTIFACT_FILES` in `ki-ccl`'s [scripts/validate_context.py](https://github.com/ki-group-gmbh/ki-ccl/blob/main/scripts/validate_context.py) enforces the required three, as data rather than a per-domain branch so the next domain needing a shape is a dictionary entry.
`decisions.md` and `timeline.md` are deliberately not required: a project in discovery has settled no arguments and committed to no dates, and empty files would be worse than absent ones.

### Listing projects, and why there is no `list_projects` tool

`get_domain_manifest("projects")` **is** the listing tool.
A per-domain listing tool would duplicate it and cost a property worth keeping: `list_domains` is the single cold-start entry point, adding a domain touches no tool signature, and once `list_projects` exists the next question is why not `list_policies`.

What the listing did lack was status.
The manifest carries id, title, kind and description, while stage and health live inside `status.md`, so "which projects are at risk" would have meant fetching every project in full and reading prose.

So the packager lifts the `status.md` header into the manifest as a `progress` object, exactly as it already derives `version_id` by running git at package time:

```json
{ "id": "acme-onboarding", "title": "Acme Onboarding",
  "progress": { "as_of": "2026-08-28", "stage": "delivery", "health": "at risk" } }
```

Derived rather than duplicated into `artifact.yaml`, because two copies of a fact drift and `status.md` is the one an engineer actually edits.
One call now answers which projects are off track, what sits in each stage, and whose status has gone stale.
`progress` is present only on artifacts that have a `status.md`, so its presence is the check for "does this report progress" and no other domain carries dead keys.
`ki-ccl`'s [scripts/status_header.py](https://github.com/ki-group-gmbh/ki-ccl/blob/main/scripts/status_header.py) defines the format once, because the gate and the packager reading it separately would drift.

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

The conventions, the stage and health vocabularies, and what an update is meant to cost are in [project-status-reporting](https://github.com/ki-group-gmbh/ki-ccl/blob/main/domains/method/project-status-reporting/README.md).
One thing there is still unresolved and matters before real project data lands: nothing yet fails when a status goes stale.
Read access is no longer broad, but note what that does and does not buy, in [access control](#access-control): the MCP read path is scoped to the caller, while the repository these files live in is not.

Domain ids are close to permanent.
`version_id` comes from the last commit touching `domains/<domain>/<artifact>/`, so renaming a domain restamps every artifact inside it at once.
Usage records are keyed on domain plus id, so a rename also splits an artifact's history into two unrelated series.

## Quick start

```bash
make install     # .venv + dependencies
make test        # this repo's own suite (server + auth); some tests need ki-ccl checked
                  # out as a sibling and skip themselves otherwise - see below
make demo        # walk the acceptance demo end to end, against CONTEXT_ROOT
make serve-http  # MCP server on http://127.0.0.1:8000/mcp
make inspector   # serve, and open MCP Inspector against it
make usage       # what was looked up, and what was asked for and missed
```

`CONTEXT_ROOT` (default `../ki-ccl/dist/staging`) is where every `serve*`/`demo`/`dashboard` target reads its corpus from.
Check out [ki-ccl](https://github.com/ki-group-gmbh/ki-ccl) as a sibling of this repo and run `make package` there first:

```bash
git clone git@github.com:ki-group-gmbh/ki-ccl.git ../ki-ccl
(cd ../ki-ccl && make install && make package)
make serve-http
```

A few tests want the real `access-policy.yaml` too, to prove authorization holds against real data rather than only synthetic fixtures (`test_access.py`, `test_demo_principals.py`).
They read it from that same sibling checkout and skip themselves, cleanly, when it is absent; override the path with `KI_CCL_ROOT`.

## Layout

```
server/artifacts.py                 the read path. Lifts into ki-mcp unchanged.
server/access.py                    authorization: domains as compartments, sensitivity as a ladder.
server/usage.py                     usage logging middleware. Lifts into ki-mcp.
server/identity.py                  claims -> Principal, and the only module holding a raw Entra oid.
server/mcp_server.py                throwaway harness. Replaced by ki-mcp's tool registry.
scripts/demo.py                     the acceptance demo, over a real MCP client
scripts/usage_report.py             reads logs/usage.jsonl
server/dashboard.py                 usage aggregation, and the /dashboard routes when they are switched on
scripts/dashboard.py                the same dashboard, on loopback, needing no server
deploy/                             Terraform: the app, the Files share, the ki-ccl publish runner
```

The corpus itself (`domains/`, `access-policy.yaml`, the validation gate and packager) lives in [ki-ccl](https://github.com/ki-group-gmbh/ki-ccl) - see its README for that layout.

## Adding an artifact

Artifacts live in `ki-ccl`, not here. See [its README](https://github.com/ki-group-gmbh/ki-ccl#adding-an-artifact).

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
Fetch with `get_artifact("team", ["<id>"])`. NOT APPROVED: `draft` (a-vas,
b-ali, ...). Say so in any answer drawn from these, and never present
unreviewed content as settled fact.
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
 "domain":"marketing","id":"ki-performance-corporate-identity","outcome":"found",
 "version_id":"b0f9dd0a...","file_count":2,"duration_ms":0.7}
{"ts":"2026-08-28T09:04:11Z","event":"context_use","tool":"get_artifact",
 "domain":"team","id":"parental-leave","outcome":"not_found","duration_ms":0.4}
```

**The miss records are the reason this exists.**
An id that is asked for repeatedly and never found is a document somebody needs and nobody has written, and nothing else in the system carries that signal.
`make usage` puts them in their own table.

Two properties worth keeping:

- **No tool knows about it.** It is FastMCP middleware, so adding a tool needs no logging code and no allowlist entry, and logging cannot fall out of step with the tool list. A logging failure is swallowed: it must stay an annoyance, never an outage.
- **The caller is recorded as a keyed pseudonym.** `actor` is `HMAC(key, oid)` truncated to 12 hex characters, minted in [server/identity.py](server/identity.py), which is the only module that ever holds a raw Entra object id. With no key configured the field is absent rather than null, so a stretch of log without one cannot be mistaken for a person, and neither authentication nor authorization depends on the key. A pseudonym is still personal data: the Art. 6 basis, the 90-day retention and the works council position are in [access control](#access-control) below. What is still never recorded: a name, an email, a UPN, an IP, the raw object id, or the user's question.

Sinks are stderr plus `logs/usage.jsonl`. Set `CONTEXT_USAGE_LOG=""` to leave stderr as the only one, which is what production wants: stdout is already collected by Log Analytics and a file would be a second store to own. The deployed dashboard is the one exception and sets a path again, because it reads the file rather than the workspace; that copy lives in the container's writable layer and dies with the revision, so Log Analytics stays the only store anybody owns.

### The dashboard

Two hosts, one page, one set of functions.

`make dashboard` serves [scripts/dashboard.py](scripts/dashboard.py) on `:8010`.
It reads the log file directly, so it needs no MCP server running, and it re-polls every three seconds so records appear while you test.

`KI_ICL_DASHBOARD=1` mounts the same thing at `/dashboard` on the server's own port, behind whatever ingress the server is behind (`make serve-http-dashboard` locally, and `dashboard_enabled = true` in [deploy/](deploy/) for the Azure app).
Unset, not one of those routes is registered, which is the default everywhere.

All aggregation is pure Python functions over a list of records in [server/dashboard.py](server/dashboard.py), covered by tests; [server/dashboard.html](server/dashboard.html) only renders what it is handed, and addresses its endpoints relative to wherever it was served from so one file works under both hosts.

**The mounted dashboard is not access-controlled, and two things about it are worse than merely unauthenticated.**
Its catalog panel comes from `live_catalog`, which reads `_manifest.json` directly and lists every artifact id in the corpus regardless of who may read it, because it describes the corpus rather than answering a caller.
And `/dashboard/purge` rewrites the usage log, which is the only route in the system that edits the audit trail.
So the ingress in front of it is the entire gate.
That is a disclosed trade for a demo reached from one allow-listed address, on the same footing as `external_ingress_enabled` itself, and it is why the server refuses the switch outright in `entra` mode and Terraform refuses the same combination: an IP allow-list stops being a defensible gate the moment there are real identities to gate.

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

Starts the server and opens MCP Inspector against [mcp-inspector.json](mcp-inspector.json), which lists this repo's entries and nothing else.
All of them serve the same four tools; `ki-icl-http` is the one `make inspector` starts, and it is unauthenticated.

To see access control working, pick an identity rather than a transport:

```bash
make serve-http-demo          # then use ki-icl-http-demo, or swap the token in the header
make serve-as                 # stdio, enforcing, as the `baseline` identity
make serve-as DEV_PRINCIPAL=broad
```

`ki-icl-http-demo` carries `Authorization: Bearer demo-token-baseline`.
Change the id in that header to any row in [config/demo_principals.yaml](config/demo_principals.yaml) to see the same corpus through different eyes: `broad` reads the projects, `baseline` sees the domain and neither project in it, `no-grants` sees nothing at all.

Note what `make serve-http` and plain `make serve` do **not** do: with no authentication configured there is no identity to key authorization on, so grants are observed rather than applied and nothing is withheld.
Every would-be denial is still logged with `effect: observed`, and `make usage` puts them in their own table, so the dry run is real even though the door is open.
That is why enforcement locally needs `serve-as`: it supplies an identity, and therefore something to enforce against.

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

## Access control

Reads are scoped to the caller's identity, taken from an Entra-issued JWT.
Two axes, and they are not interchangeable:

- **Domains are compartments.** A role reads a domain or it does not. Grants live in `ki-ccl`'s [access-policy.yaml](https://github.com/ki-group-gmbh/ki-ccl/blob/main/access-policy.yaml), keyed on Entra **app role** values rather than group object ids, so the file reads as English in a pull request and no IdP identifier ships with the corpus.
- **`sensitivity` is a ladder**, compared only within a domain. Every artifact declares one of `internal`, `restricted` or `confidential`, required and gated the same way `review` is. A principal's level for a domain is the maximum across their roles, so gaining a role never removes access.

There is deliberately no wildcard and no global top level.
That is what makes "HR reads personnel material and leadership does not" expressible, and it is what makes a new domain fail `make validate` until the pull request adding it also decides who reads it.

A denied domain returns an honest `forbidden`, because the eight domain **names** are KI group's business functions and are disclosed to any authenticated caller by design.
A denied artifact is silently absent, because artifact ids are customer names.
A domain with every row filtered is byte-identical to an empty one, or the difference would be an enumeration oracle.

### What this defends, and what it does not

**This layer defends the MCP read path. It does not defend the content.**

This repository is a git repository. Everything labelled `restricted` stays readable by anyone who can clone it, and `version_id` in a payload is a commit SHA pointing straight at it.
`sensitivity: confidential` is an ISO 27001 A.5.12 classification and a read-path control, not an access control for personal data.
Content that genuinely needs one needs repository separation.

The failure mode it does fix is specific and real: an agent doing broad discovery for one colleague pulls a customer name and `health: at risk` into a context window, and from there into a summary, an email, or a model provider's logs.

### Transports

`--http` is the only transport that can be authenticated.
Over stdio the client spawns the process, owns its stdin, and runs it as the invoking user, so a bearer token would prove nothing that OS process ownership does not already decide.
`KI_ICL_AUTH` selects `entra`, `demo` or `off`, and `--http` refuses to start without one of them, so a forgotten variable is a server that does not come up rather than one that serves HR content to anyone who can reach the port.

The server is a pure OAuth **resource server**: `RemoteAuthProvider` over `JWTVerifier`, holding a public JWKS URL and no secret of any kind.
Claude authenticates against Entra; this server only ever verifies the result.

`config/demo_principals.yaml` holds fake principals for local testing, gated on `environment: local`, a `demo-token-` prefix on every token, `KI_ICL_AUTH=demo`, and a loopback bind.
Because `StaticTokenVerifier` passes its claims through unchanged, the demo tokens exercise the same `claims -> Principal` code as a real Entra token rather than a mock.

### Before this runs in production

Engineering does not block on these, but production does.

- **Art. 6(1)(f)** legitimate interests, with a written balancing test. Consent is not available in an employment relationship. German employee data is additionally governed by §26 BDSG and Art. 88 GDPR, and the DPO confirms the provision and its numbering rather than this file.
- **§87(1) no. 6 BetrVG co-determination.** A per-person read log over `team` content (personnel and staffing profiles) is objectively *suitable for* monitoring employee behaviour, and suitability is assessed regardless of intent. Betriebsrat consultation, in practice a Betriebsvereinbarung, comes before the log has data in it. [project-status-reporting](https://github.com/ki-group-gmbh/ki-ccl/blob/main/domains/method/project-status-reporting/README.md#progress-belongs-to-the-project-never-to-a-person) already reasoned about this same boundary for status reporting, and the consultation goes better carrying that reasoning.
- **The control that makes the purpose limitation real: no tool here aggregates by actor.** Neither the dashboard nor `make usage` has a per-actor ranking, volume chart, or actor dimension, and `test_no_aggregation_groups_by_actor` fails if one is added. This log answers "did access control hold", never "how much did this person read".
- **Retention 90 days**, enforced where the store is: production sets `CONTEXT_USAGE_LOG=""` so Log Analytics is the only store, with workspace retention set there and the workspace pinned to an EU region. The deployed dashboard sets that variable to a path again, and does not change this: the file is in the container's writable layer, it dies with the revision, nothing reads it but `/dashboard`, and Log Analytics still receives every line. It is also mutually exclusive with `auth_mode = "entra"`, so it can never coexist with a log that records an actor. Token validation is local and the JWKS fetch carries only public signing keys, so there is no Art. 44 transfer in the auth path.
- **Not an Annex III high-risk AI system**, and the reason is worth keeping: no automated decision about a person, no profile, no ranking or score. Any future feature that ranks, scores or compares people changes that classification.
- `KI_ICL_AUDIT_KEY` is a secret. Key Vault or a container-app secret, never this repo and never `~/.claude.json`, which is a plaintext home-directory file that gets backed up and synced.

## Deploying

[deploy/](deploy/) holds Terraform for Azure Container Apps, and [deploy/README.md](deploy/README.md) is the runbook.
Target is the KI-PER Data Platform Sandbox, resource group `ki-icl-sandbox`, Germany West Central.

The shape, and the two things worth knowing before reading the rest:

- **Ingress is internal.** The environment gets a private IP, so only a VNet linked to its private DNS zone can reach it. That is what makes running with `KI_ICL_AUTH=off` defensible at first: the network is the control. It also means claude.ai and Claude Desktop cannot reach it, only Claude Code from inside that network.
- **The corpus lives on a mounted, privately-reachable Azure Files share**, published separately from the image and validated by the same gate CI runs before it lands there. This used to be baked into the image, which made the image tag answer "which corpus was served on Tuesday"; that property now belongs to ki-ccl's own history instead, traded for not needing a rebuild on every content edit. Nothing snapshots the share: reconstructing any past corpus means checking out the commit that produced it and republishing, which is also how a bad publish is rolled back.

Five stages, each of which leaves something working: infrastructure with a placeholder image, then the publish runner, then the real image with grants observed rather than enforced, then the audit key, then Entra.
The fourth and fifth are where personal data starts being processed, and the runbook says so at the point where it happens.

## Publishing

The corpus reaches the Files share with no human in the loop, on every merge to `ki-ccl`'s `main`: [ki-ccl](https://github.com/ki-group-gmbh/ki-ccl)'s `publish.yml` validates and packages the content, then uploads it from a self-hosted GitHub Actions runner that lives inside `kiicl-vnet` (`deploy/runner.tf`), authenticating as that runner's own managed identity.
There is no public network path to the share at all, at any point - see [deploy/README.md](deploy/README.md#stage-2-the-publish-runner) for how the runner is provisioned and credentialed.

A pull request against `ki-ccl` runs the same validate-and-package steps as a dry run and never uploads, so a broken corpus fails before it merges.

## What this POC leaves out

Everything here is deliberate, and each item is cheap to add once it is wanted.

| Not built | Why, and what it costs |
|---|---|
| Azure blob fetch, ETag guard, cache swap | Parametrizing `ki-mcp/server/utils/skills_source.py`, which already works in production and is the pattern `ki-dev-skills` uses to publish to `ki-mcp`. Nothing new to design, but now it has a hard constraint: the cache must hold **raw** manifests, because a cached filtered one served to a second caller is a cross-principal disclosure. This deployment sidesteps it entirely by mounting a Files share instead, so a content change is a share update rather than a cache invalidation - see [Publishing](#publishing). |
| Registration in ki-mcp | Move `server/artifacts.py` to `ki-mcp/server/utils/artifacts.py` and the three tool functions to `server/tools/artifact_tools.py`. |
| A test proving `ki-ccl`'s packaged output is servable by this server's real read path | Used to be one test here, packaging this repo's own content and feeding it straight into `server/access.py` and `server/artifacts.py`. Splitting the corpus into `ki-ccl` broke that - it needs both repos in one process. `test_access.py` and `test_demo_principals.py` keep the authorization half of this (the real policy, checked out from `ki-ccl` as a sibling); the packaging half is `ki-ccl`'s own `test_this_repos_own_content_packages`. What is not proven anywhere any more: that the two halves compose. A true end-to-end check would hit the deployed server after a real publish. |
| Ownership, `CODEOWNERS`, approval routing | Deferred by decision. `owner` is already served as `null` at both levels, so adding it is data, not a schema change. The routing rule above is chosen to agree with it when it lands. |
| A fixed `kind` vocabulary | Issue #20 OQ-3, undecided. `kind` is a non-empty free string, and two values are in use: `guideline` and `methodology`. Seven domains will pull it in more directions, so it is worth settling soon, but generalising before there is content to generalise from would be the wrong order. |
| Search, similarity, resolution from task context | Deferred on a stated trigger. Adding it would break the property in the first section. |
| Aggregating usage beyond a local file | `logs/usage.jsonl` is POC scaffolding. In ki-mcp the records go to stderr and Log Analytics collects them, so the file sink is deleted, not ported. |
| Binary assets | See the text-only rule above. |

## Lifting the read path into ki-mcp

`server/artifacts.py` is written against the spec's section 7.3 signatures so it moves unchanged apart from two lines:

- `ARTIFACTS_ROOT` becomes `settings.artifacts_dir`.
- `_file_payload` is deleted in favour of `from utils.file_payload import file_payload`.

`visible_domains()` and `_readable_domains()` are the two scoping seams and must stay the only way a read path resolves a domain or reads a manifest row.
Per-identity scoping landed on them, and their whole value is that no read path can be written that forgets it.
That is now mechanical rather than aspirational: `principal` is a required keyword-only parameter on every payload function, pinned by `test_every_payload_function_requires_a_principal`, and the unfiltered row list is read in exactly one place, pinned by an `ast` test.

One invariant to carry across the move, because it is the thing most likely to break: when the Azure blob fetch and ETag cache land, **the cache holds raw manifests and filtering happens after the cache, per request, always.**
A cached filtered manifest served to a second principal is a cross-principal disclosure, and it is invisible today only because `domain_manifest()` re-reads from disk on every call.
