# What we build

## The engineering brain

One governed layer, built over the systems that **stay** the systems of record — repos, tracker, wiki, pipelines. Nothing gets migrated.

The layer holds three things: what the organization **knows**, how it actually **works**, and who may **do what**. Above it sit the people and their agents as one unit — every agent acts on behalf of a person — reading from the layer, doing the job, and writing the result back.

**That write-back is the whole difference.** Read-only is a mirror; read-and-write is a memory. And because every use improves the layer, it compounds: each process rebuilt makes the next one cheaper.

### The two shortcuts that fail, and why

**"Point a chatbot at the wiki."** Everything in there was written for humans, it is read-only, and most of what an agent actually needs was never written down at all.

**"Let the power users run."** Fifty private agent setups drifting apart, no shared memory, no identity, no audit trail — and everything a power user learns leaves when they do.

It is not a retrieval problem. It is a missing foundation.

## What sits on the foundation

Five capabilities, which is what "governed" concretely means:

- **A shared context layer** — a knowledge graph with retrieval over the engineering sources
- **Governed tools** — shared integrations to the source systems, built once and used by every agent
- **Agent identity and audit** — every agent under a governed identity, with one responsible person behind it, and every action auditable
- **Governed model access** — model serving kept swappable, routed through one controlled path
- **SDLC artifacts remodeled agent-readable** — a spec-first baseline

Where a client already runs an enterprise LLM layer that passes our foundation-fit check, the loops run on it. We do not rebuild what they own.

## The two reference loops

Both are running at a multi-thousand-person energy company, and neither is a point solution — they draw on the same foundation, and everything they learn lands back on it. Which is precisely why the second loop was cheaper than the first, and the third will be cheaper again.

### The delivery loop ("software factory")

Spec-driven delivery, redesigned around three actors. **Humans write the spec and review the pull request; agents do everything in between.**

A spec — the what and the why — enters the queue. The agent implements in isolation, with every step and its cost visible on the board. A draft pull request arrives with tests green. The human approves, or comments and the agent iterates. Humans remain the sole merge authority.

Static prompts are gone: the loop runs on reusable skills, and it improves them with every run.

### The discovery loop

A requester submits a raw idea in their own words. The agent runs a guided intake — asking until the picture is complete, remembering the conversation — scores the idea for similarity against every existing one so overlap gets linked rather than duplicated, and drafts the complete discovery pack from the shared context.

The pack reaches development substantially ready. The remaining bottleneck is how fast requesters answer the agent's questions — not the agent.

## How this maps to the engagement

The delivery arc is the main offering's, unchanged: **Discover → Lighthouse → Champion enablement → Foundation**, four modules on five gates, each gate a real continue / pause / stop decision, the next module scoped and priced on the previous gate's evidence.

For an engineering audience the stages land as: *you just talk* (one painful process, real files, the loop co-created live) → *one lighthouse* (that loop to production, shadow mode first, the owner signs off) → *your people, enabled* (a cell of five to seven of their engineers and functional staff build the next loops themselves; the gate is the day a non-engineer ships a reusable agent) → *the Foundation* (everything consolidates, the learning loop goes live, they own the operating layer).

Full detail: the `how-we-deliver.md` file in the `ai-native-transformation` artifact, also in the `offerings` domain.
