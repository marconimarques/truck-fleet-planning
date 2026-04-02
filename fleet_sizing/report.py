"""Console and CSV output for fleet sizing results."""
from __future__ import annotations

import csv
import dataclasses
import re
from pathlib import Path

from .results import Scope41Result, Scope42Result, Scope43Result

ScopeResult = Scope41Result | Scope42Result | Scope43Result


def print_console(results: list[ScopeResult], verbose: bool = False) -> None:
    print("\n" + "=" * 72)
    print("  FLEET SIZING RESULTS")
    print("=" * 72)

    for result in results:

        if isinstance(result, Scope41Result):
            print(f"\n{'-' * 60}")
            print("  Scope 4.1 -- Static Lane-by-Lane  (most conservative)")
            print(f"  Total Fleet  : {result.total_trucks} trucks")
            print(f"  Total Volume : {result.total_monthly_volume_tons:>10,.0f} t/month")
            print(f"  Freight Cost : ${result.total_freight_cost_usd:>10,.0f} /month")

            if verbose:
                infeasible = [lr for lr in result.lane_results if "infeasible" in lr.notes]
                if infeasible:
                    print(f"\n  WARNING: {len(infeasible)} lane(s) infeasible (cycle exceeds window):")
                    for lr in infeasible:
                        print(f"    {lr.cp_name} -> {lr.terminal_name}  cycle={lr.cycle_time_hours:.2f}h")
                print(
                    f"\n  {'CP':<22} {'Terminal':<20} "
                    f"{'Dist(km)':>9} {'Cycle(h)':>9} {'Trips/d':>7} {'Trucks':>7}  Notes"
                )
                print(f"  {'-'*22} {'-'*20} {'-'*9} {'-'*9} {'-'*7} {'-'*7}")
                for lr in result.lane_results:
                    if lr.monthly_demand_tons > 0:
                        print(
                            f"  {lr.cp_name:<22} {lr.terminal_name:<20} "
                            f"{lr.distance_km:>9.1f} {lr.cycle_time_hours:>9.2f} "
                            f"{lr.trips_per_truck_per_day:>7.2f} {lr.trucks_needed:>7d}  {lr.notes}"
                        )

        elif isinstance(result, Scope42Result):
            print(f"\n{'-' * 60}")
            print("  Scope 4.2 -- Volume-Weighted Average Cycle")
            print(f"  Weighted Cycle : {result.weighted_cycle_time_hours:.3f} h")
            print(f"  Trips/Truck/Day: {result.trips_per_truck_per_day:.2f}")
            print(f"  Total Fleet    : {result.total_trucks} trucks")
            print(f"  Total Volume   : {result.total_monthly_volume_tons:>10,.0f} t/month")
            print(f"  Freight Cost   : ${result.total_freight_cost_usd:>10,.0f} /month")

        elif isinstance(result, Scope43Result):
            print(f"\n{'-' * 60}")
            print("  Scope 4.3 -- MILP Optimisation  (24-day horizon, HiGHS)")
            print(f"  Status         : {result.status}")
            print(f"  Total Fleet    : {result.total_trucks} trucks")
            print(
                f"  Delivered      : {result.total_delivered_tons:>10,.0f} t  "
                f"(contracted: {result.total_monthly_volume_tons:,.0f} t)"
            )
            print(f"  Freight Cost   : ${result.total_freight_cost_usd:>10,.0f} /month")
            print(f"  Solve Time     : {result.solve_time_seconds:.1f} s")
            if result.objective_bound is not None:
                print(f"  MIP Gap        : {abs(result.total_trucks - result.objective_bound):.2f}")

            if verbose and result.trip_schedule:
                sample = result.trip_schedule[:15]
                print(f"\n  Trip schedule (first {len(sample)} non-zero entries):")
                print(
                    f"  {'Day':>4}  {'CP':<22} {'Terminal':<20} "
                    f"{'Loaded':>7} {'Repo':>6} {'Tons':>8}"
                )
                for e in sample:
                    print(
                        f"  {e.day:>4}  {e.cp_name:<22} {e.terminal_name:<20} "
                        f"{e.loaded_trips:>7} {e.repo_trips:>6} {e.payload_delivered_tons:>8.0f}"
                    )

    print("\n" + "=" * 72 + "\n")


_FIELDS = [
    "scope", "cp_name", "terminal_name", "distance_km",
    "cycle_time_hours", "trips_per_truck_per_day", "monthly_demand_tons",
    "daily_demand_tons", "trucks_needed", "trips_month",
    "monthly_operational_cost_usd",
    "milp_status", "milp_solve_time_s", "notes", "scenario_description",
]


def _summary_row(result: ScopeResult, description: str = "", **extra) -> dict:
    row: dict = {f: "" for f in _FIELDS}
    row["scope"] = result.scope
    row["cp_name"] = "ALL"
    row["terminal_name"] = "ALL"
    row["monthly_demand_tons"] = round(result.total_monthly_volume_tons, 1)
    row["trucks_needed"] = result.total_trucks
    row["trips_month"] = result.total_trips_month
    row["monthly_operational_cost_usd"] = round(result.monthly_operational_cost_usd, 2)
    row["scenario_description"] = description
    row.update(extra)
    return row


def _next_version(output_dir: Path, stem: str) -> Path:
    """Return the next non-existing versioned path: <stem>_v01.csv, _v02.csv, ..."""
    v = 1
    while True:
        path = output_dir / f"{stem}_v{v:02d}.csv"
        if not path.exists():
            return path
        v += 1


def write_csv(results: list[ScopeResult], output_dir: Path, label: str = "output", description: str = "") -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fpath = _next_version(output_dir, f"{label}_output")

    rows: list[dict] = []

    for result in results:
        if isinstance(result, Scope41Result):
            for lr in result.lane_results:
                rows.append({
                    "scope": result.scope,
                    "cp_name": lr.cp_name,
                    "terminal_name": lr.terminal_name,
                    "distance_km": round(lr.distance_km, 2),
                    "cycle_time_hours": round(lr.cycle_time_hours, 3),
                    "trips_per_truck_per_day": round(lr.trips_per_truck_per_day, 3),
                    "monthly_demand_tons": round(lr.monthly_demand_tons, 1),
                    "daily_demand_tons": round(lr.daily_demand_tons, 2),
                    "trucks_needed": lr.trucks_needed,
                    "trips_month": lr.trips_month,
                    "milp_status": "N/A",
                    "milp_solve_time_s": "",
                    "notes": lr.notes,
                })
            rows.append(_summary_row(
                result,
                description=description,
                trips_per_truck_per_day=round(result.trips_per_truck_per_day, 3),
                milp_status="N/A",
                notes="TOTAL -- lane-by-lane",
            ))

        elif isinstance(result, Scope42Result):
            rows.append(_summary_row(
                result,
                description=description,
                cycle_time_hours=round(result.weighted_cycle_time_hours, 3),
                trips_per_truck_per_day=round(result.trips_per_truck_per_day, 3),
                milp_status="N/A",
                notes=f"weighted_cycle={result.weighted_cycle_time_hours:.3f}h",
            ))

        elif isinstance(result, Scope43Result):
            rows.append(_summary_row(
                result,
                description=description,
                trips_per_truck_per_day=round(result.trips_per_truck_per_day, 3),
                milp_status=result.status,
                milp_solve_time_s=result.solve_time_seconds,
                notes=f"delivered={result.total_delivered_tons:.0f}t",
            ))

    with open(fpath, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    # Write MILP trip schedule to a separate file if present
    s43 = next((r for r in results if isinstance(r, Scope43Result) and r.trip_schedule), None)
    if s43 is not None:
        sched_path = _next_version(output_dir, f"{label}_milp_schedule")
        sched_fields = ["cp_name", "terminal_name", "day",
                        "round_trips", "one_way_trips", "loaded_trips",
                        "repo_trips", "payload_delivered_tons"]
        with open(sched_path, "w", newline="", encoding="utf-8") as sf:
            sw = csv.DictWriter(sf, fieldnames=sched_fields)
            sw.writeheader()
            for entry in s43.trip_schedule:
                row = dataclasses.asdict(entry)
                row["loaded_trips"] = entry.loaded_trips  # property, not in asdict
                sw.writerow(row)
    return fpath
