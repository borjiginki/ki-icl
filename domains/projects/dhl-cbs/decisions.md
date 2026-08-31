# DHL CBS: decisions

> **Demo content. Every decision below is invented.**

Newest first.
One entry per decision that would otherwise be re-argued, with what was chosen, what it was chosen over, and who decided.

A decision is recorded here when reversing it would cost more than a day, or when someone outside the team would reasonably ask why it is that way.
Everything else is just work and belongs in [status.md](status.md).

### 2026-08-22: rounding happens once, at consolidation

**Chosen:** round to the invoice currency once, when the consolidated line is produced.
**Over:** rounding each unit's contribution before consolidating.
**Decided by:** DHL finance reviewer, on our recommendation.

Rounding per unit and then summing produced consolidated totals that differed from the customer's own sum by up to nine cents on a three-unit invoice.
Nine cents is not a money problem, it is a reconciliation problem: every such invoice becomes a query.

**Cost of the choice:** each unit's reported contribution no longer sums exactly to its own ledger figure, so the reconciliation report carries an explicit rounding line. That line was added rather than hidden.

### 2026-07-31: CBS consumes rated data and never rates

**Chosen:** country systems keep rating and tariff logic; CBS treats the rated amount as an input.
**Over:** moving rating into CBS for the participating units.
**Decided by:** DHL programme manager.

Rating carries the tariff and contract logic and differs per unit.
Absorbing it would have made CBS the system of record for pricing, which is a far larger programme than the one that was funded.

**Cost of the choice:** a rating error in a country system flows through CBS unchanged, and CBS cannot detect it. Accepted knowingly. The reconciliation report shows the per-unit input so an error is at least visible.

### 2026-07-03: agreed interchange format rather than per-unit adapters

**Chosen:** one interchange format every unit conforms to.
**Over:** an adapter per unit against whatever each already exports.
**Decided by:** KI group engagement lead, agreed by the DHL programme manager.

Adapters would have been faster for the first two units and would have made every later wave a new build.
Since wave one is the reference the later waves are decided on, a format that later units conform to was worth the slower start.

**Cost of the choice:** each unit does work before its feed can go live, and that is exactly what the BE dependency is. The risk in [status.md](status.md) is a direct consequence of this decision, and was foreseen when it was taken.

### 2026-06-09: three units in wave one, not five

**Chosen:** DE, NL and BE.
**Over:** adding FR and PL to wave one.
**Decided by:** DHL programme manager.

Three units cover the two currencies and the one cross-border case that make consolidation hard.
Two more would have added volume without adding a new problem to solve.
