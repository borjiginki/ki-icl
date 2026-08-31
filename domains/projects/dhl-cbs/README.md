# DHL CBS

> **Demo content. Every fact below is invented.**
> The engagement name is real; nothing else on this page is.
> The acronym expansion, the scope, the dates, the people and the numbers were written to show what a project artifact answers, not to describe the actual engagement.
> Whoever leads this project has to replace this file before anyone answers a real question from it.

## What we are doing

DHL bills freight through separate systems in each country unit.
The same customer shipping from three countries receives three invoices on three cycles, in three formats, and reconciliation happens by hand at the end of every month.

CBS, the Central Billing System, is one settlement service that takes rated shipment data from every country unit and produces a single consolidated invoice per customer per cycle.

KI group is building the settlement and consolidation service and the reconciliation reporting on top of it.
DHL owns the country-side data feeds and the customer-facing invoice presentation.

Agreed measure of success, from the kickoff document:

- One invoice per customer per cycle across all participating country units.
- Month-end reconciliation for a participating unit takes under one day, down from five.
- No manual re-keying of shipment data anywhere in the path.

## Scope

In scope:

- Ingest of rated shipment data from participating country units, in the agreed interchange format.
- Consolidation and settlement engine, including currency handling and the agreed rounding rules.
- Reconciliation reporting for finance users in each unit.
- Migration of historical billing data for the first two units.
- Handover documentation and training for the DHL operations team.

Out of scope, each of which has been asked for at least once:

- Rating and tariff calculation. It stays in the country systems, and CBS consumes the result.
- Customer-facing invoice layout and delivery. DHL owns presentation.
- Dunning and collections.
- Tax determination. DHL's existing tax engine remains the authority, and CBS carries its output rather than recomputing it.
- Country units beyond the three in the first wave. Later waves are a separate decision, not an option inside this one.

## Why it matters commercially

This is the first engagement with this customer at programme scale rather than project scale.
The first wave is the reference the later waves are decided on, which is why the scope line above is defended rather than negotiated case by case.

The commercial shape, man days and rates live with sales.
This artifact describes the work.

## Where to look next

| Question | File |
|---|---|
| What stage is it at, and is it healthy | [status.md](status.md) |
| Who is working on it | [team.md](team.md) |
| What was decided, and why | [decisions.md](decisions.md) |
| What is due when, and what has slipped | [timeline.md](timeline.md) |

The layout is the same for every project in this domain, by convention.
See [project status reporting](../../value-creation/project-status-reporting/README.md).
