"""Demand relocation optimizer.

Finds the allocation of demand across collection points that minimises
the fleet size, expressed as the continuous-relaxation proxy:

    minimise  Σ_{i,j}  x[i,j] · cycle_time[i,j] / monthly_cap

subject to:
    • Terminal totals preserved  : Σ_i x[i,j] = baseline total  ∀ j
    • CP capacity                : Σ_j x[i,j] ≤ cp_capacity[i]  ∀ i
    • Non-negative               : x[i,j] ≥ 0
    • Only active pairs          : x[i,j] = 0  if demand_mask[i,j] is False
                                   (no new CP–Terminal routes created)

The result is a list of ScenarioChange objects ready for apply_scenario(),
plus a breakdown of which lanes gain or lose volume.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from .data import PreprocessedData
from .scenario import ScenarioChange


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class LaneReallocation:
    cp_name: str
    terminal_name: str
    demand_before: float
    demand_after: float

    @property
    def delta(self) -> float:
        return self.demand_after - self.demand_before


@dataclass
class RelocationResult:
    status: str
    lane_changes: list[LaneReallocation] = field(default_factory=list)
    trucks_continuous_before: float = 0.0
    trucks_continuous_after: float = 0.0
    changes: list[ScenarioChange] = field(default_factory=list)

    @property
    def saving(self) -> float:
        return self.trucks_continuous_before - self.trucks_continuous_after

    @property
    def saving_pct(self) -> float:
        if self.trucks_continuous_before == 0:
            return 0.0
        return self.saving / self.trucks_continuous_before * 100


# ── Optimiser ─────────────────────────────────────────────────────────────────

def optimize_relocation(pre: PreprocessedData) -> RelocationResult:
    """Return the optimal demand reallocation that minimises continuous fleet size.

    Only existing CP→Terminal lanes (demand_mask True) are candidates, so the
    optimiser cannot invent new logistics routes.
    """
    n_cp = len(pre.cp_names)
    n_t  = len(pre.terminal_names)
    monthly_cap = pre.effective_hours * pre.availability * pre.working_days * pre.payload

    # Only lanes that already carry demand AND fit within the shift window are
    # candidates — routing demand to window-infeasible lanes would worsen scope
    # 4.1/4.2 results since those scopes cannot serve them.
    candidates = [
        (i, j)
        for i in range(n_cp)
        for j in range(n_t)
        if pre.demand_mask[i, j] and pre.window_feasible[i, j]
    ]

    candidates_by_term = {j: [i for (i, jj) in candidates if jj == j] for j in range(n_t)}
    candidates_by_cp   = {i: [j for (ii, j) in candidates if ii == i] for i in range(n_cp)}

    prob = pulp.LpProblem("DemandRelocation", pulp.LpMinimize)

    x = {
        (i, j): pulp.LpVariable(f"x_{i}_{j}", lowBound=0)
        for (i, j) in candidates
    }

    # Objective: minimise continuous truck count proxy
    prob += pulp.lpSum(
        x[i, j] * float(pre.cycle_time[i, j]) / monthly_cap
        for (i, j) in candidates
    ), "min_trucks"

    # Terminal totals must be preserved
    for j in range(n_t):
        if candidates_by_term[j]:
            total_j = float(pre.monthly_demand[:, j].sum())
            prob += (
                pulp.lpSum(x[i, j] for i in candidates_by_term[j]) == total_j,
                f"terminal_total_{j}",
            )

    # CP capacity: total across both terminals cannot exceed monthly capacity
    for i in range(n_cp):
        if candidates_by_cp[i]:
            prob += (
                pulp.lpSum(x[i, j] for j in candidates_by_cp[i]) <= float(pre.cp_capacity_monthly[i]),
                f"cp_cap_{i}",
            )

    prob.solve(pulp.HiGHS(msg=False))

    if prob.status != 1:
        return RelocationResult(status=pulp.LpStatus[prob.status])

    before = sum(
        float(pre.monthly_demand[i, j]) * float(pre.cycle_time[i, j]) / monthly_cap
        for (i, j) in candidates
    )
    after = float(pulp.value(prob.objective) or 0.0)

    lane_changes: list[LaneReallocation] = []
    changes: list[ScenarioChange] = []

    for (i, j) in candidates:
        new_val = round(max(0.0, float(pulp.value(x[i, j]) or 0.0)), 1)
        old_val = float(pre.monthly_demand[i, j])
        if abs(new_val - old_val) >= 0.5:
            lane_changes.append(LaneReallocation(
                cp_name=pre.cp_names[i],
                terminal_name=pre.terminal_names[j],
                demand_before=old_val,
                demand_after=new_val,
            ))
            changes.append(ScenarioChange(
                change_type="demand",
                value=new_val,
                targets=[pre.cp_names[i]],
                terminal_targets=[pre.terminal_names[j]],
            ))

    # Sort: by terminal, then biggest movers first
    lane_changes.sort(key=lambda r: (r.terminal_name, r.delta))

    return RelocationResult(
        status="Optimal",
        lane_changes=lane_changes,
        trucks_continuous_before=before,
        trucks_continuous_after=after,
        changes=changes,
    )
