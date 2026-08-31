# Project status reporting

> **Placeholder content.**
> The layout and the vocabularies below are a first proposal and have not been agreed with anyone who runs projects at KI group.
> The reasoning is real; the specific words are up for argument.

Every project in the `projects` domain uses the same file layout.
That uniformity is the whole point: it is what lets a question be answered from the right file without anyone having learned that project's particular habits.

## The layout

| File | Answers | Changes |
|---|---|---|
| `README.md` | What are we doing, and what is in and out of scope | Rarely. Scope changes are events. |
| `status.md` | What stage is it at, is it healthy, what moved, what is blocked | Every update. This is the only file most updates touch. |
| `team.md` | Who is working on it, and who decides what on the customer side | Occasionally |
| `decisions.md` | What was decided and why, and what it cost | When a decision is taken |
| `timeline.md` | What is due when, and what has slipped | When a date moves |

`README.md`, `status.md` and `team.md` are required.
`decisions.md` and `timeline.md` appear when there is something true to put in them: a project in discovery has committed to no dates and settled no arguments, and empty files would be worse than absent ones.

## Stage

One word, from this list and no other.
A stage is where the work is, not how it is going.

| Stage | Means |
|---|---|
| `discovery` | Working out what the work is. No estimate committed. |
| `mobilising` | Scope agreed. Team, access and environments being set up. |
| `delivery` | Building the agreed scope. |
| `handover` | Built. Transferring to the customer: documentation, training, support arrangement. |
| `closed` | Finished. The artifact stays, because "what did we do for them in 2026" is a real question. |
| `stopped` | Ended without finishing. Says so, and says why. A project that quietly disappears teaches nobody anything. |

## Health

One word, separate from stage, because a project can be in delivery and in trouble at the same time.
Collapsing the two is how "in progress" ends up meaning nothing.

| Health | Means |
|---|---|
| `on track` | The committed dates hold with the float that was planned. |
| `at risk` | A committed date has no float left, or a dependency is late. Names what would have to happen to recover. |
| `blocked` | Work has stopped and cannot restart without someone outside the team acting. Names that person and what is needed. |

`at risk` and `blocked` are not failures to be softened.
The reason this domain exists is so that a CEO or project manager learns about them without having to ask, and a status that only ever reads `on track` is a status nobody needs to read.

## The as-of date is mandatory

Every `status.md`, `team.md` and `timeline.md` starts with **As of YYYY-MM-DD**.

This is not decoration.
The context layer stamps every answer with a `version_id`, but that stamp is deliberately opaque: it is compared for equality to detect staleness and is never parsed, so it cannot tell anyone that a status is three months old.
Without a date in the content, a stale status answers confidently and there is no way to know.

So the date carries the recency, and an agent answering from this domain is expected to say it: not "the project is at risk" but "as of 28 August it was at risk".

## What an update costs

The design target is that an update is four lines and takes under five minutes, because a report that costs an engineer an hour gets written once and then stops.

1. Change the three lines at the top of `status.md`: the as-of date, the stage, the health.
2. Append one dated entry to the log at the bottom, in the same voice you would use telling a colleague.
3. Only if something actually changed: touch `team.md`, `decisions.md` or `timeline.md`.

Nothing about hours, percentages, or completion estimates.
A percentage complete is a number that has to be invented weekly and cannot be checked, and asking for one is how the report becomes a chore that gets faked.

How the update actually reaches the repository is not decided yet.
Whatever it turns out to be, it has to set the as-of date automatically, because the one field that must never be wrong is also the one a human will forget.

## Progress belongs to the project, never to a person

`team.md` names people so that someone asking "who is on this" can reach them.
It records nothing about how much an individual delivered, how fast, or how they compare.

Two reasons, and the second is the weaker one.

A record that lets an employer assess individual performance is personal data, and in Germany it carries a works council dimension under the BDSG.
Arriving at such a record as a side effect of status reporting, without anyone deciding to build it, is the wrong way to get there.
If KI group ever wants individual reporting, that is a deliberate decision with a documented legal basis, not a consequence of this file's layout.

The weaker reason: nobody asking these questions wants it.
"What stage is DHL CBS at" is answered by the project, and a per-person breakdown makes the answer longer and less useful.

## Before real project data lands here

Two things are unresolved, and both belong to whoever owns this domain rather than to this document.

**Access is broad by construction.** Every caller who can reach the MCP sees every domain; per-identity scoping is deferred to a separate issue. Customer names, scope, rates and slipped commitments in this domain are readable by anyone with access to the server. For real engagements that has to be checked against the NDA or AVV in force before the content is written, not after.

**Freshness is unenforced.** Nothing currently fails when a `status.md` goes stale. The as-of date makes staleness visible to a reader, which is the important half, but it does not make it visible to anyone who is not reading. A freshness signal belongs with the update mechanism, which is why it is not bolted onto the validation gate now.
