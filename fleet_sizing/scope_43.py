"""Scope 4.3 — MILP fleet sizing model (24-day horizon, HiGHS solver).

Model formulation
-----------------
Objective: minimize N (total truck fleet)

Two trip types distinguish same-day returns from overnight stays:

  f_rt[i,j,d]  round-trip trucks: CP_i → Terminal_j → CP_i  (ends at CP_i)
  f_ow[i,j,d]  one-way trucks:    CP_i → Terminal_j           (ends at Terminal_j)
  r[j,i,d]     repo trucks:       Terminal_j → CP_i            (empty move)
  r_cp[i,i2,d] CP-to-CP repo:    CP_i → CP_i2                 (empty move, i ≠ i2)

Round trips allow trucks to serve a lane every day (same as static models).
One-way + repo enable cross-CP repositioning, which can reduce total fleet.

Decision variables:
  N              integer >= 1         total fleet
  f_rt[i,j,d]   continuous >= 0      round-trip loaded trips
  f_ow[i,j,d]   continuous >= 0      one-way loaded trips
  r[j,i,d]      continuous >= 0      Terminal→CP repositioning trips
  r_cp[i,i2,d]  continuous >= 0      CP→CP repositioning trips (i ≠ i2)
  pos_cp[i,d]   continuous >= 0      trucks at CP_i at start of day d
  pos_t[j,d]    continuous >= 0      trucks at Terminal_j at start of day d

All variables except N are continuous — consistent with the reference formula's
fractional-trip assumption. This reduces the problem to a single-integer MILP
that solves in seconds.

Constraints:
  C1  Demand         sum_d (f_rt + f_ow)[i,j,d] * payload >= monthly_demand[i,j]
  C2  Terminal cap.  sum_{i,d} (f_rt + f_ow)[i,j,d] * payload <= terminal_capacity[j]
  C3  CP capacity    sum_{j,d} (f_rt + f_ow)[i,j,d] * payload <= cp_capacity[i]
  C5  Time budget CP sum_j (f_rt*cycle + f_ow*ow_time)[i,j,d] <= pos_cp[i,d]*avail*eff_hours
  C6  Repo count     sum_i r[j,i,d] <= pos_t[j,d]*avail
  C7  Repo time      sum_i r[j,i,d]*repo_time[j,i] <= pos_t[j,d]*avail*eff_hours
  C8  Fleet total    sum_i pos_cp[i,d] + sum_j pos_t[j,d] == N  (all days)
  C9  Flow CP        pos_cp[i,(d+1)%D] = pos_cp[i,d] - sum_j f_ow[i,j,d] + sum_j r[j,i,d]
                                         - sum_i2 r_cp[i,i2,d] + sum_i2 r_cp[i2,i,d]
  C10 Flow Terminal  pos_t[j,(d+1)%D]  = pos_t[j,d]  + sum_i f_ow[i,j,d] - sum_i r[j,i,d]

The schedule is cyclical (day-25 ≡ day-1 via modular indexing).
C4 (trip count ≤ trucks) is omitted — with continuous variables C5 is the sole
binding constraint, matching the reference formula.
"""
from __future__ import annotations

import time as _time

import pulp

from .data import PreprocessedData
from .results import CapacityViolation, MILPTrip, Scope43Result


def solve(
    pre: PreprocessedData,
    solver_time_limit: int = 300,
    solver_gap: float = 0.01,
    verbose: bool = False,
) -> Scope43Result:
    t0 = _time.perf_counter()

    n_cp = len(pre.cp_names)
    n_t = len(pre.terminal_names)
    D = pre.working_days  # 24

    cps = range(n_cp)
    terms = range(n_t)
    days = range(D)

    # Pre-solve feasibility check: demand vs capacity
    # Catches the case where C1 (demand) and C2/C3 (capacity) directly conflict,
    # which would make the solver return Infeasible with no explanation.
    capacity_violations: list[CapacityViolation] = []
    for i in cps:
        total_cp_demand = float(pre.monthly_demand[i].sum())
        cap = float(pre.cp_capacity_monthly[i])
        if total_cp_demand > cap:
            capacity_violations.append(CapacityViolation(
                node_name=pre.cp_names[i], node_type="cp",
                demand=total_cp_demand, capacity=cap,
            ))
    for j in terms:
        total_t_demand = float(pre.monthly_demand[:, j].sum())
        cap = float(pre.terminal_capacity_monthly[j])
        if total_t_demand > cap:
            capacity_violations.append(CapacityViolation(
                node_name=pre.terminal_names[j], node_type="terminal",
                demand=total_t_demand, capacity=cap,
            ))
    if capacity_violations:
        return Scope43Result(
            status="Infeasible",
            capacity_violations=capacity_violations,
            solve_time_seconds=round(_time.perf_counter() - t0, 2),
            total_monthly_volume_tons=float(pre.monthly_demand.sum()),
            total_freight_cost_usd=float((pre.monthly_demand * pre.freight_cp_terminal).sum()),
        )

    # Active lanes: all lanes with demand > 0 (window filter omitted — MILP uses
    # one-way trips so a lane is not infeasible just because the full round-trip
    # cycle exceeds the operating window; the time-budget constraints handle it)
    active = [(i, j) for i in cps for j in terms if pre.demand_mask[i, j]]

    # Precomputed per-CP and per-terminal active lane lookups (avoids O(|active|)
    # scans inside every constraint-building loop)
    active_by_cp   = {i: [j for (ii, j) in active if ii == i] for i in cps}
    active_by_term = {j: [i for (i, jj) in active if jj == j] for j in terms}

    # One-way trip time: cycle_time - empty return leg
    ow_time = pre.cycle_time - pre.drive_time_empty.T  # shape (n_cp, n_t)
    repo_time = pre.drive_time_empty                   # shape (n_t, n_cp)

    # ── Problem ──────────────────────────────────────────────────────────────
    prob = pulp.LpProblem("FleetSizing_4_3", pulp.LpMinimize)

    N = pulp.LpVariable("N", lowBound=1, cat="Integer")

    f_rt = {
        (i, j, d): pulp.LpVariable(f"frt_{i}_{j}_{d}", lowBound=0, cat="Continuous")
        for (i, j) in active for d in days
    }
    f_ow = {
        (i, j, d): pulp.LpVariable(f"fow_{i}_{j}_{d}", lowBound=0, cat="Continuous")
        for (i, j) in active for d in days
    }
    r = {
        (j, i, d): pulp.LpVariable(f"r_{j}_{i}_{d}", lowBound=0, cat="Continuous")
        for j in terms for i in cps for d in days
    }
    cp_pairs = [(i, i2) for i in cps for i2 in cps if i != i2]
    r_cp = {
        (i, i2, d): pulp.LpVariable(f"rcp_{i}_{i2}_{d}", lowBound=0, cat="Continuous")
        for (i, i2) in cp_pairs for d in days
    }
    pos_cp = {
        (i, d): pulp.LpVariable(f"pos_cp_{i}_{d}", lowBound=0, cat="Continuous")
        for i in cps for d in days
    }
    pos_t = {
        (j, d): pulp.LpVariable(f"pos_t_{j}_{d}", lowBound=0, cat="Continuous")
        for j in terms for d in days
    }

    prob += N, "minimize_fleet"

    # C1: Monthly demand satisfaction
    for i, j in active:
        prob += (
            pulp.lpSum(f_rt[i, j, d] + f_ow[i, j, d] for d in days) * pre.payload
            >= pre.monthly_demand[i, j],
            f"demand_{i}_{j}",
        )

    # C2: Terminal monthly throughput capacity
    for j in terms:
        if active_by_term[j]:
            prob += (
                pulp.lpSum(
                    (f_rt[i, j, d] + f_ow[i, j, d]) * pre.payload
                    for i in active_by_term[j] for d in days
                ) <= pre.terminal_capacity_monthly[j],
                f"term_cap_{j}",
            )

    # C3: CP monthly throughput capacity
    for i in cps:
        if active_by_cp[i]:
            prob += (
                pulp.lpSum(
                    (f_rt[i, j, d] + f_ow[i, j, d]) * pre.payload
                    for j in active_by_cp[i] for d in days
                ) <= pre.cp_capacity_monthly[i],
                f"cp_cap_{i}",
            )

    # C5: Time budget at CP (C4 omitted — continuous variables make it redundant)
    for i in cps:
        if active_by_cp[i]:
            for d in days:
                time_used = pulp.lpSum(
                    f_rt[i, j, d] * float(pre.cycle_time[i, j])
                    + f_ow[i, j, d] * float(ow_time[i, j])
                    for j in active_by_cp[i]
                ) + pulp.lpSum(
                    r_cp[i, i2, d] * float(pre.drive_time_cp_cp[i, i2])
                    for i2 in cps if i2 != i
                )
                prob += time_used <= pos_cp[i, d] * pre.availability * pre.effective_hours, f"time_cp_{i}_{d}"

    # C6 & C7: Repo count and time budget at terminal
    for j in terms:
        for d in days:
            prob += pulp.lpSum(r[j, i, d] for i in cps) <= pos_t[j, d] * pre.availability, f"repo_count_{j}_{d}"
            prob += (
                pulp.lpSum(r[j, i, d] * float(repo_time[j, i]) for i in cps)
                <= pos_t[j, d] * pre.availability * pre.effective_hours,
                f"repo_time_{j}_{d}",
            )

    # C8: Fleet total (all trucks accounted for every day)
    for d in days:
        prob += (
            pulp.lpSum(pos_cp[i, d] for i in cps) + pulp.lpSum(pos_t[j, d] for j in terms) == N,
            f"fleet_total_{d}",
        )

    # C9: Flow conservation at CP_i — cyclical
    for i in cps:
        for d in days:
            d_next = (d + 1) % D
            ow_out = pulp.lpSum(f_ow[i, j, d] for j in active_by_cp[i]) if active_by_cp[i] else 0
            repos_in = pulp.lpSum(r[j, i, d] for j in terms)
            rcp_out = pulp.lpSum(r_cp[i, i2, d] for i2 in cps if i2 != i)
            rcp_in = pulp.lpSum(r_cp[i2, i, d] for i2 in cps if i2 != i)
            prob += pos_cp[i, d_next] == pos_cp[i, d] - ow_out + repos_in - rcp_out + rcp_in, f"flow_cp_{i}_{d}"

    # C10: Flow conservation at Terminal_j — cyclical
    for j in terms:
        for d in days:
            d_next = (d + 1) % D
            ow_in = pulp.lpSum(f_ow[i, j, d] for i in active_by_term[j]) if active_by_term[j] else 0
            repos_out = pulp.lpSum(r[j, i, d] for i in cps)
            prob += pos_t[j, d_next] == pos_t[j, d] + ow_in - repos_out, f"flow_t_{j}_{d}"

    # ── Solve ────────────────────────────────────────────────────────────────
    solver = pulp.HiGHS(msg=verbose, timeLimit=solver_time_limit, gapRel=solver_gap)
    prob.solve(solver)

    solve_time = _time.perf_counter() - t0
    status = pulp.LpStatus[prob.status]
    total_monthly = float(pre.monthly_demand.sum())
    total_freight = float((pre.monthly_demand * pre.freight_cp_terminal).sum())

    if prob.status != 1:  # 1 = Optimal
        return Scope43Result(
            status=status,
            solve_time_seconds=round(solve_time, 2),
            total_monthly_volume_tons=total_monthly,
            total_freight_cost_usd=total_freight,
        )

    n_opt = int(round(pulp.value(N)))
    schedule: list[MILPTrip] = []
    total_delivered = 0.0
    total_op_cost = 0.0
    total_trips_month = 0.0  # accumulated as float; rounded to int at return

    for i, j in active:
        one_way_km = float(pre.dist_cp_terminal[i, j])

        for d in days:
            # Use continuous LP values for cost and volume — rounding introduces
            # demand gaps and cost errors when fractional trips are large.
            rt_f   = max(0.0, float(pulp.value(f_rt[i, j, d]) or 0))
            ow_f   = max(0.0, float(pulp.value(f_ow[i, j, d]) or 0))
            repo_f = max(0.0, float(pulp.value(r[j, i, d]) or 0))

            loaded_f = rt_f + ow_f
            total_delivered += loaded_f * pre.payload
            total_trips_month += loaded_f

            # Variable cost: rt = round-trip (2×km), ow = one-way loaded (1×km),
            # repo = terminal→CP empty return (1×km) — all km charged at variable rate
            total_op_cost += (rt_f * 2 + ow_f + repo_f) * one_way_km * pre.variable_cost_per_km

            # Round only for the human-readable schedule display
            rt_i   = max(0, int(round(rt_f)))
            ow_i   = max(0, int(round(ow_f)))
            repo_i = max(0, int(round(repo_f)))
            if rt_i > 0 or ow_i > 0 or repo_i > 0:
                schedule.append(MILPTrip(
                    cp_name=pre.cp_names[i],
                    terminal_name=pre.terminal_names[j],
                    day=d + 1,
                    round_trips=rt_i,
                    one_way_trips=ow_i,
                    repo_trips=repo_i,
                    payload_delivered_tons=float((rt_i + ow_i) * pre.payload),
                ))

    # CP-to-CP repositioning km (variable cost applies to all driving)
    for i, i2 in cp_pairs:
        cp_cp_km = float(pre.dist_cp_cp[i, i2])
        for d in days:
            rcp_val = max(0, pulp.value(r_cp[i, i2, d]) or 0)
            total_op_cost += rcp_val * cp_cp_km * pre.variable_cost_per_km

    # Fixed cost: one entry per truck per month regardless of utilisation
    total_op_cost += n_opt * pre.fixed_cost_per_truck_month

    total_op_cost += n_opt * pre.working_days * pre.overtime_hours * pre.cost_overtime_driver

    obj_bound: float | None = None
    try:
        obj_bound = float(prob.bestBound) if hasattr(prob, "bestBound") else None
    except Exception:
        pass

    trips_per_day = total_trips_month / (n_opt * pre.working_days) if n_opt > 0 else 0.0

    return Scope43Result(
        status=status,
        total_trucks=n_opt,
        objective_bound=obj_bound,
        solve_time_seconds=round(solve_time, 2),
        trip_schedule=schedule,
        total_monthly_volume_tons=total_monthly,
        total_delivered_tons=round(total_delivered, 1),
        total_freight_cost_usd=total_freight,
        total_trips_month=int(round(total_trips_month)),
        trips_per_truck_per_day=round(trips_per_day, 3),
        monthly_operational_cost_usd=round(total_op_cost, 2),
    )
