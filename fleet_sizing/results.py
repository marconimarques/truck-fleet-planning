from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CapacityViolation:
    node_name: str
    node_type: str   # "cp" or "terminal"
    demand: float    # tons/month
    capacity: float  # tons/month


@dataclass
class LaneResult:
    cp_name: str
    terminal_name: str
    distance_km: float
    freight_rate_usd_t: float
    cycle_time_hours: float
    trips_per_truck_per_day: float
    monthly_demand_tons: float
    daily_demand_tons: float
    trucks_needed: int
    monthly_freight_cost_usd: float
    trips_month: int = 0
    notes: str = ""


@dataclass
class Scope41Result:
    scope: str = "4.1_lane_by_lane"
    lane_results: list[LaneResult] = field(default_factory=list)
    total_trucks: int = 0
    total_monthly_volume_tons: float = 0.0
    total_freight_cost_usd: float = 0.0
    total_trips_month: int = 0
    trips_per_truck_per_day: float = 0.0
    monthly_operational_cost_usd: float = 0.0


@dataclass
class Scope42Result:
    scope: str = "4.2_weighted_cycle"
    weighted_cycle_time_hours: float = 0.0
    trips_per_truck_per_day: float = 0.0
    total_trucks: int = 0
    total_monthly_volume_tons: float = 0.0
    total_freight_cost_usd: float = 0.0
    total_trips_month: int = 0
    monthly_operational_cost_usd: float = 0.0


@dataclass
class MILPTrip:
    cp_name: str
    terminal_name: str
    day: int
    round_trips: int
    one_way_trips: int
    repo_trips: int
    payload_delivered_tons: float

    @property
    def loaded_trips(self) -> int:
        return self.round_trips + self.one_way_trips


@dataclass
class Scope43Result:
    scope: str = "4.3_milp"
    status: str = ""
    total_trucks: int = 0
    objective_bound: float | None = None
    solve_time_seconds: float = 0.0
    trip_schedule: list[MILPTrip] = field(default_factory=list)
    total_monthly_volume_tons: float = 0.0
    total_delivered_tons: float = 0.0
    total_freight_cost_usd: float = 0.0
    total_trips_month: int = 0
    trips_per_truck_per_day: float = 0.0
    monthly_operational_cost_usd: float = 0.0
    capacity_violations: list[CapacityViolation] = field(default_factory=list)
