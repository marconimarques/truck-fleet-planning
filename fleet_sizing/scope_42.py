"""Scope 4.2 — Static fleet calculation with volume-weighted average cycle time.

A single weighted-average cycle time is computed across all active lanes,
weighted by monthly demand share. One global fleet count is then derived.
Less conservative than 4.1 because it allows the same truck to serve multiple lanes.
"""
import math

import numpy as np

from .data import PreprocessedData
from .results import Scope42Result


def solve(pre: PreprocessedData) -> Scope42Result:
    mask = pre.demand_mask & pre.window_feasible
    total_freight = float((pre.monthly_demand * pre.freight_cp_terminal).sum())
    total_monthly = float(pre.monthly_demand.sum())
    total_trips = int(np.ceil(pre.monthly_demand[pre.demand_mask] / pre.payload).sum())

    if not mask.any():
        return Scope42Result(total_monthly_volume_tons=total_monthly,
                             total_freight_cost_usd=total_freight,
                             total_trips_month=total_trips)

    active_demand = pre.monthly_demand[mask]
    active_cycles = pre.cycle_time[mask]
    total_active = active_demand.sum()

    weighted_cycle = float((active_demand / total_active * active_cycles).sum())
    monthly_cap = pre.effective_hours * pre.availability * pre.working_days * pre.payload
    trips_per_truck = pre.effective_hours * pre.availability / weighted_cycle
    total_trucks = math.ceil(total_active * weighted_cycle / monthly_cap)

    active_dist = pre.dist_cp_terminal[mask]
    total_km = float((np.ceil(active_demand / pre.payload) * 2 * active_dist).sum())
    op_cost = (
        total_trucks * pre.fixed_cost_per_truck_month
        + total_km * pre.variable_cost_per_km
        + total_trucks * pre.working_days * pre.overtime_hours * pre.cost_overtime_driver
    )

    return Scope42Result(
        weighted_cycle_time_hours=weighted_cycle,
        trips_per_truck_per_day=float(trips_per_truck),
        total_trucks=total_trucks,
        total_monthly_volume_tons=total_monthly,
        total_freight_cost_usd=total_freight,
        total_trips_month=total_trips,
        monthly_operational_cost_usd=round(op_cost, 2),
    )
