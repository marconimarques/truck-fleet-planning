"""Load Excel inputs and compute all derived arrays used by the three solvers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Derived data container — consumed by all three solvers
# ---------------------------------------------------------------------------

@dataclass
class PreprocessedData:
    cp_names: list[str]
    terminal_names: list[str]

    # Travel time arrays (hours)
    drive_time_loaded: np.ndarray   # shape (n_cp, n_t): CP→Terminal
    drive_time_empty: np.ndarray    # shape (n_t, n_cp): Terminal→CP
    drive_time_cp_cp: np.ndarray    # shape (n_cp, n_cp): CP→CP (diagonal = 0)

    # Full round-trip cycle time per lane (hours)
    cycle_time: np.ndarray          # shape (n_cp, n_t)

    # Effective net working hours per truck per day (shift - lunch, ± overtime)
    # Used by the MILP time-budget constraint.
    effective_hours: float

    # Node operating windows: min(cp_window, terminal_window)
    effective_window: np.ndarray    # shape (n_cp, n_t)

    # Feasibility masks
    demand_mask: np.ndarray         # shape (n_cp, n_t): True where monthly demand > 0
    window_feasible: np.ndarray     # shape (n_cp, n_t): True if cycle fits in window

    # Demand
    monthly_demand: np.ndarray      # shape (n_cp, n_t): tons/month
    daily_demand: np.ndarray        # shape (n_cp, n_t): monthly / working_days

    # Capacity limits
    cp_capacity_monthly: np.ndarray        # shape (n_cp,)
    terminal_capacity_monthly: np.ndarray  # shape (n_t,)

    # Distance and freight (for reporting)
    dist_cp_terminal: np.ndarray    # shape (n_cp, n_t): km
    freight_cp_terminal: np.ndarray # shape (n_cp, n_t): $/t

    # Truck operational cost rates
    variable_cost_per_km: float        # $/km — all variable costs (fuel, tires, maintenance, etc.)
    fixed_cost_per_truck_month: float  # $/truck/month — fixed costs (depreciation, wages, IPVA, etc.)
    cost_overtime_driver: float        # $/extra hour/truck — from driver-policy.xlsx

    # Per-component cost breakdown for display
    # {"variable": [(name, $/km), ...], "fixed": [(name, $/truck/month), ...]}
    cost_breakdown: dict[str, list[tuple[str, float]]]

    # Scalars
    working_days: int
    payload: float
    availability: float
    working_hours_per_day: float    # full shift length (e.g. 10 h) — used by static formulas

    # Raw inputs stored for what-if scenario recomputation
    speed_loaded: float             # km/h
    speed_empty: float              # km/h
    lunch_hours: float              # h
    overtime_hours: float           # h
    cp_load_times: np.ndarray       # shape (n_cp,): load time at each CP (h)
    t_unload_times: np.ndarray      # shape (n_t,): unload time at each terminal (h)
    dist_cp_cp: np.ndarray          # shape (n_cp, n_cp): CP-to-CP distances (km)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _time_to_hours(v) -> float | None:
    """Convert datetime.time, numeric, or None to float hours."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, time):
        return v.hour + v.minute / 60 + v.second / 3600
    raise TypeError(f"Cannot convert {type(v).__name__} to hours")


def _read(path: Path, sheet: str, header: int | None = 0, **kwargs) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet, header=header, engine="openpyxl", **kwargs)


def _align(values: np.ndarray, src_rows: list, src_cols: list,
           dst_rows: list, dst_cols: list) -> np.ndarray:
    """Reindex a matrix to match target row/col order. Missing entries default to 0."""
    ri = {r: i for i, r in enumerate(src_rows)}
    ci = {c: i for i, c in enumerate(src_cols)}
    out = np.zeros((len(dst_rows), len(dst_cols)))
    for i, r in enumerate(dst_rows):
        for j, c in enumerate(dst_cols):
            if r in ri and c in ci:
                out[i, j] = values[ri[r], ci[c]]
    return out


_CP_PREFIX = "Collection_Point"
_TERMINAL_PREFIX = "Unload_Terminal"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_data(data_dir: Path) -> PreprocessedData:
    """Load all xlsx input files and return a PreprocessedData object."""
    data_dir = Path(data_dir)

    # ── Truck spec ──────────────────────────────────────────────────────────
    truck_path = data_dir / "truck-specification.xlsx"
    speed_map = {
        str(row.iloc[0]).strip(): float(row.iloc[1])
        for _, row in _read(truck_path, "Speed").iterrows()
        if pd.notna(row.iloc[0]) and pd.notna(row.iloc[1])
    }
    # Handle "Empity Truck" typo in source data
    speed_loaded = speed_map.get("Loaded Truck", speed_map.get("Loaded_Truck", 45.0))
    speed_empty = speed_map.get(
        "Empity Truck",
        speed_map.get("Empty Truck", speed_map.get("Empity_Truck", 60.0)),
    )
    payload = float(_read(truck_path, "Truck_Payload").iloc[0, 1])
    availability = float(_read(truck_path, "Truck_Availability").iloc[0, 1])

    # ── Driver policy ───────────────────────────────────────────────────────
    drv_path = data_dir / "driver-policy.xlsx"
    hours_df = _read(drv_path, "Driver_Working_Hours")
    working_hours = _time_to_hours(hours_df.iloc[0, 1])
    lunch_hours = _time_to_hours(hours_df.iloc[0, 2])
    overtime_hours = _time_to_hours(hours_df.iloc[1, 1])
    cost_overtime_driver = float(hours_df.iloc[4, 1])
    working_days = int(_read(drv_path, "Driver_Working_Days").iloc[0, 1])

    effective_hours = working_hours - lunch_hours + overtime_hours

    # ── Terminals & collection points ───────────────────────────────────────
    cp_names, t_names = [], []
    cp_load_times, t_unload_times = [], []
    cp_windows, t_windows = [], []
    cp_capacities, t_capacities = [], []

    for _, row in _read(data_dir / "terminals-specification.xlsx", "Specification").iterrows():
        name = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
        if not name or name == "nan":
            continue
        capacity = float(row.iloc[1])
        window = _time_to_hours(row.iloc[5]) - _time_to_hours(row.iloc[4])
        if name.startswith(_CP_PREFIX):
            cp_names.append(name)
            cp_capacities.append(capacity)
            cp_windows.append(window)
            cp_load_times.append(_time_to_hours(row.iloc[6]))
        elif name.startswith(_TERMINAL_PREFIX):
            t_names.append(name)
            t_capacities.append(capacity)
            t_windows.append(window)
            t_unload_times.append(_time_to_hours(row.iloc[7]))

    # ── Demand ──────────────────────────────────────────────────────────────
    demand_df = pd.read_excel(
        data_dir / "transportation-cargo-volume.xlsx",
        sheet_name="Tons_Per_Month", header=0, index_col=0, engine="openpyxl",
    )
    demand_df = demand_df.dropna(axis=0, how="all").dropna(axis=1, how="all").fillna(0)
    monthly_demand = _align(
        demand_df.values.astype(float),
        [str(i).strip() for i in demand_df.index],
        [str(c).strip() for c in demand_df.columns],
        cp_names, t_names,
    )
    daily_demand = monthly_demand / working_days

    # ── Distance CP → CP ────────────────────────────────────────────────────
    cp_cp_path = data_dir / "collection-to-collection-point.xlsx"
    cp_cp_dist_df = pd.read_excel(cp_cp_path, sheet_name="Distance_Matrix",
                                  header=0, index_col=0, engine="openpyxl")
    cp_cp_dist_df = cp_cp_dist_df.dropna(axis=0, how="all").dropna(axis=1, how="all").fillna(0)
    cp_cp_rows = [str(i).strip() for i in cp_cp_dist_df.index]
    cp_cp_cols = [str(c).strip() for c in cp_cp_dist_df.columns]
    dist_cp_cp = _align(cp_cp_dist_df.values.astype(float), cp_cp_rows, cp_cp_cols, cp_names, cp_names)
    drive_time_cp_cp = dist_cp_cp / speed_empty  # shape (n_cp, n_cp), diagonal = 0

    # ── Truck operational cost rates ────────────────────────────────────────
    cost_df = _read(data_dir / "truck-operational-cost.xlsx", "Method", header=None)
    # Layout: col A = item name, col B = value ($/km for variable; $/truck/month for fixed)
    # Sections: "Variable Costs" header → component rows → "Total Variable" formula row
    #           "Fixed Costs per Month" header → component rows → "Total fixed" formula row
    variable_items: list[tuple[str, float]] = []
    fixed_items: list[tuple[str, float]] = []
    _section: str | None = None
    for _, _row in cost_df.iterrows():
        _name = str(_row.iloc[0]).strip() if pd.notna(_row.iloc[0]) else ""
        if not _name or _name == "nan":
            continue
        if "Variable Costs" in _name:
            _section = "variable"
        elif "Fixed Costs" in _name:
            _section = "fixed"
        elif _name.startswith("Total"):
            pass  # skip formula rows — we sum components directly
        elif isinstance(_row.iloc[1], (int, float, np.integer, np.floating)):
            if _section == "variable":
                variable_items.append((_name, float(_row.iloc[1])))
            elif _section == "fixed":
                fixed_items.append((_name, float(_row.iloc[1])))

    variable_cost_per_km = sum(v for _, v in variable_items)
    fixed_cost_per_truck_month = sum(v for _, v in fixed_items)
    cost_breakdown = {"variable": variable_items, "fixed": fixed_items}

    # ── Distance & freight (CP → Terminal) ──────────────────────────────────
    cpt_path = data_dir / "collection-point-to-terminal.xlsx"
    with pd.ExcelFile(cpt_path, engine="openpyxl") as _wb_cpt:
        dist_df = _wb_cpt.parse("Distance_Matrix", header=0, index_col=0)
        dist_df = dist_df.dropna(axis=0, how="all").dropna(axis=1, how="all").fillna(0)
        mat_rows = [str(i).strip() for i in dist_df.index]
        mat_cols = [str(c).strip() for c in dist_df.columns]
        dist_cp_t = _align(dist_df.values.astype(float), mat_rows, mat_cols, cp_names, t_names)

        # Freight matrix is optional — default to zeros if sheet is absent
        if "Freight_Matrix" in _wb_cpt.sheet_names:
            freight_df = _wb_cpt.parse("Freight_Matrix", header=0, index_col=0)
            freight_df = freight_df.dropna(axis=0, how="all").dropna(axis=1, how="all").fillna(0)
            freight_cp_t = _align(freight_df.values.astype(float), mat_rows, mat_cols, cp_names, t_names)
        else:
            freight_cp_t = np.zeros((len(cp_names), len(t_names)))

    # ── Compute derived arrays ──────────────────────────────────────────────
    load_t = np.array(cp_load_times)    # (n_cp,)
    unload_t = np.array(t_unload_times) # (n_t,)
    drive_loaded = dist_cp_t / speed_loaded         # (n_cp, n_t)
    drive_empty = (dist_cp_t / speed_empty).T       # (n_t, n_cp)
    cycle_time = (
        load_t[:, np.newaxis]
        + drive_loaded
        + unload_t[np.newaxis, :]
        + drive_empty.T
    )

    cp_win = np.array(cp_windows)
    t_win = np.array(t_windows)
    effective_window = np.minimum(cp_win[:, np.newaxis], t_win[np.newaxis, :])

    demand_mask = monthly_demand > 0
    window_feasible = (cycle_time < effective_window) & demand_mask

    return PreprocessedData(
        cp_names=cp_names,
        terminal_names=t_names,
        drive_time_loaded=drive_loaded,
        drive_time_empty=drive_empty,
        drive_time_cp_cp=drive_time_cp_cp,
        cycle_time=cycle_time,
        effective_hours=effective_hours,
        effective_window=effective_window,
        demand_mask=demand_mask,
        window_feasible=window_feasible,
        monthly_demand=monthly_demand,
        daily_demand=daily_demand,
        cp_capacity_monthly=np.array(cp_capacities),
        terminal_capacity_monthly=np.array(t_capacities),
        dist_cp_terminal=dist_cp_t,
        freight_cp_terminal=freight_cp_t,
        working_days=working_days,
        payload=payload,
        availability=availability,
        working_hours_per_day=working_hours,
        speed_loaded=speed_loaded,
        speed_empty=speed_empty,
        lunch_hours=lunch_hours,
        overtime_hours=overtime_hours,
        cp_load_times=np.array(cp_load_times),
        t_unload_times=np.array(t_unload_times),
        dist_cp_cp=dist_cp_cp,
        variable_cost_per_km=variable_cost_per_km,
        fixed_cost_per_truck_month=fixed_cost_per_truck_month,
        cost_overtime_driver=cost_overtime_driver,
        cost_breakdown=cost_breakdown,
    )
