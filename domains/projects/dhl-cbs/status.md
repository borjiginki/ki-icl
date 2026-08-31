# DHL CBS: status

> **Demo content. Every fact below is invented.**

**As of 2026-08-28.**
**Stage:** delivery.
**Health:** at risk.

The settlement engine is on track and the first two country feeds are live in the test environment.
The risk is on the DHL side: the third unit's historical data extract has slipped twice and now sits inside the float before the September milestone.

Anyone repeating this status should say the date it was made.
If today is more than two weeks past the as-of date above, treat it as unconfirmed and ask the engagement lead.

## Why the health is at risk

The BE unit's historical extract was due 2026-08-08 and is now expected 2026-09-05.
Migration validation for that unit needs three working weeks, so the 2026-09-30 first-consolidated-run milestone has no float left.

This is a dependency, not a build problem.
Raised with the DHL programme manager on 2026-08-19 and again on 2026-08-26.
The decision on whether to run the September milestone with two units instead of three is due 2026-09-08.

## What moved since the last update

- Settlement engine handles multi-currency consolidation, including the agreed rounding rules. Signed off by the DHL finance reviewer on 2026-08-22.
- DE and NL shipment feeds ingest end to end in the test environment.
- Reconciliation report for a single unit is in review with finance users.
- Rounding-rule question from 2026-08-14 is closed, recorded in [decisions.md](decisions.md).

## What is next

- Cross-unit reconciliation report, in progress, target 2026-09-12.
- BE feed ingest, blocked on the extract above.
- Load test at projected wave-one volume, target 2026-09-19.
- Handover documentation, started, target 2026-10-17.

## Blockers

| Blocker | Owner | Since | Effect if unresolved |
|---|---|---|---|
| BE historical extract not delivered | DHL programme manager | 2026-08-08 | September milestone runs with two units, and wave one closes one unit short |

## Log

Newest first.
One entry per update, and the three lines at the top of this file are updated with it.

### 2026-08-28

Multi-currency consolidation signed off. BE extract slipped a second time, to 2026-09-05, which consumes the remaining float. Health moved from on track to at risk.

### 2026-08-14

DE and NL feeds ingesting end to end in test. Rounding rules escalated to the DHL finance reviewer as a decision rather than an assumption. Health on track.

### 2026-07-31

Settlement engine passes the agreed consolidation cases for a single unit. Stage moved from mobilising to delivery.

### 2026-07-03

Interchange format agreed with the DE and NL units. Access to the test environment granted, four weeks after kickoff.

### 2026-06-09

Kickoff. Stage mobilising.
