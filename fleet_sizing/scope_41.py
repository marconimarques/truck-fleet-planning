"""Scope 4.1 — Static lane-by-lane fleet calculation.

Each (CP, Terminal) lane is computed independently using the reference formula:
  trucks = ceil(monthly_demand * cycle_time / (effective_hours * availability * working_days * payload))

Total fleet = sum over all lanes. Most conservative approach.
"""
import math

from .data import PreprocessedData
from .results import LaneResult, Scope41Result


def solve(pre: PreprocessedData) -> Scope41Result:
    lane_results: list[LaneResult] = []
    monthly_cap = pre.effective_hours * pre.availability * pre.working_days * pre.payload

    for i in range(len(pre.cp_names)):
        for j in range(len(pre.terminal_names)):
            monthly_dem = pre.monthly_demand[i, j]
            cycle_t = pre.cycle_time[i, j]
            notes = ""
            trucks = 0
            trips = 0.0

            if monthly_dem <= 0:
                notes = "no demand"
            elif not pre.window_feasible[i, j]:
                notes = "infeasible – cycle exceeds window"
            else:
                trips = pre.effective_hours * pre.availability / cycle_t
                trucks = math.ceil(monthly_dem * cycle_t / monthly_cap)

            trips_month = math.ceil(monthly_dem / pre.payload) if monthly_dem > 0 else 0

            lane_results.append(LaneResult(
                cp_name=pre.cp_names[i],
                terminal_name=pre.terminal_names[j],
                distance_km=float(pre.dist_cp_terminal[i, j]),
                freight_rate_usd_t=float(pre.freight_cp_terminal[i, j]),
                cycle_time_hours=float(cycle_t),
                trips_per_truck_per_day=float(trips),
                monthly_demand_tons=float(monthly_dem),
                daily_demand_tons=float(pre.daily_demand[i, j]),
                trucks_needed=trucks,
                monthly_freight_cost_usd=float(monthly_dem * pre.freight_cp_terminal[i, j]),
                trips_month=trips_month,
                notes=notes,
            ))

    total_trucks = sum(r.trucks_needed for r in lane_results)
    total_trips = sum(r.trips_month for r in lane_results)
    trips_per_day = total_trips / (total_trucks * pre.working_days) if total_trucks > 0 else 0.0

    total_km = sum(r.trips_month * 2 * r.distance_km for r in lane_results if r.trucks_needed > 0)
    op_cost = (
        total_trucks * pre.fixed_cost_per_truck_month
        + total_km * pre.variable_cost_per_km
        + total_trucks * pre.working_days * pre.overtime_hours * pre.cost_overtime_driver
    )

    return Scope41Result(
        lane_results=lane_results,
        total_trucks=total_trucks,
        total_monthly_volume_tons=float(pre.monthly_demand.sum()),
        total_freight_cost_usd=sum(r.monthly_freight_cost_usd for r in lane_results),
        total_trips_month=total_trips,
        trips_per_truck_per_day=round(trips_per_day, 3),
        monthly_operational_cost_usd=round(op_cost, 2),
    )
