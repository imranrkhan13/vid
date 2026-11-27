"""Cost optimization (sections 11, 27).

Chooses the order in which eligible providers should be tried when the goal is
to finish a project while respecting a credit budget and the paid-spend policy.
It never authorizes paid spending on its own — it only ranks and *reports* what
a plan would cost, so a human/policy decision remains the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostPlanItem:
    provider_id: str
    estimated_credits: float
    is_paid: bool
    remaining_credits: float | None


@dataclass
class CostPlan:
    ordered: list[CostPlanItem] = field(default_factory=list)
    free_credits_needed: float = 0.0
    paid_credits_needed: float = 0.0
    requires_paid: bool = False
    within_budget: bool = True
    notes: list[str] = field(default_factory=list)


class CostOptimizer:
    def __init__(self, *, max_budget: float | None = None, allow_paid: bool = False) -> None:
        self.max_budget = max_budget
        self.allow_paid = allow_paid

    def plan(self, items: list[CostPlanItem]) -> CostPlan:
        """Rank candidates cheapest-first, preferring free credits.

        Ordering is stable and deterministic: free (or zero-cost) providers first,
        then by ascending estimated credits, then by id.
        """
        ordered = sorted(
            items,
            key=lambda i: (i.is_paid, i.estimated_credits, i.provider_id),
        )
        plan = CostPlan(ordered=ordered)
        total = 0.0
        for item in ordered:
            total += item.estimated_credits
            if item.is_paid:
                plan.paid_credits_needed += item.estimated_credits
            else:
                plan.free_credits_needed += item.estimated_credits
        plan.requires_paid = plan.paid_credits_needed > 0
        if self.max_budget is not None and total > self.max_budget:
            plan.within_budget = False
            plan.notes.append(
                f"estimated {total:.4f} credits exceeds budget {self.max_budget:.4f}"
            )
        if plan.requires_paid and not self.allow_paid:
            plan.notes.append("plan requires paid credits but paid spend is not approved")
        return plan

    def cheapest_eligible(self, items: list[CostPlanItem]) -> CostPlanItem | None:
        """The cheapest item that can be served from free credits, if any."""
        free = [i for i in items if not i.is_paid]
        if not free:
            return None
        return min(free, key=lambda i: (i.estimated_credits, i.provider_id))
