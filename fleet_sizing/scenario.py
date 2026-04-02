"""Apply what-if parameter changes to a copy of PreprocessedData.

Usage
-----
    from fleet_sizing.scenario import ScenarioChange, apply_scenario

    changes = [ScenarioChange(change_type="payload", delta_abs=1.5)]
    new_pre = apply_scenario(pre, changes)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .data import PreprocessedData


@dataclass
class ScenarioChange:
    """One parameter change in a what-if scenario.

    change_type options
    -------------------
    overtime            Change overtime hours (h)
    working_hours       Change full shift hours (h)
    working_days        Change working days per month
    payload             Change truck payload (tons)
    speed_loaded        Change loaded truck speed (km/h)
    speed_empty         Change empty truck speed (km/h)
    speed_both          Change both speeds simultaneously
    terminal_capacity   Change terminal monthly capacity (tons)
    unload_time         Change terminal unload time (h)
    cp_capacity         Change CP monthly capacity (tons)
    demand              Change monthly demand at selected CP→Terminal lanes (tons)
    cost_variable       Change a variable cost component ($/km); set component_name
    cost_fixed          Change a fixed cost component ($/truck/month); set component_name
    cost_overtime       Change the overtime rate ($/extra hour/truck)

    Value resolution (use exactly one)
    -----------------------------------
    value       Set to this absolute value
    delta_abs   Add this amount to current value
    delta_pct   Multiply current value by (1 + delta_pct/100)

    For speed_both, provide speed_loaded and/or speed_empty directly.
    For node-specific changes, provide targets=[<node name>, ...].
    None targets means "apply to all nodes of that type".
    For lane-specific demand changes, provide both targets (CP names) and
    terminal_targets (terminal names) to pin the change to exact CP→Terminal pairs.
    When terminal_targets is None, the change applies to all terminals served by
    the targeted CPs.
    """

    change_type: str

    value: Optional[float] = None
    delta_abs: Optional[float] = None
    delta_pct: Optional[float] = None

    targets: Optional[list[str]] = None
    terminal_targets: Optional[list[str]] = None

    # Used only with change_type == "speed_both"
    speed_loaded: Optional[float] = None
    speed_empty: Optional[float] = None

    # Used with cost_variable and cost_fixed: the component name to modify
    component_name: Optional[str] = None


# ── Name resolver ─────────────────────────────────────────────────────────────

def _match_name(candidate: str, known: list[str]) -> str | None:
    """Return the canonical name from *known* that best matches *candidate*.

    Tries exact match first, then falls back to case-insensitive comparison of
    the name with all non-alphanumeric characters stripped.  This tolerates
    minor variations like extra underscores (e.g. 'Collection_Point_01' vs
    'Collection_Point01') that a language model may introduce.
    """
    if candidate in known:
        return candidate
    def norm(s: str) -> str:
        return s.lower().replace("_", "").replace(" ", "")
    c_norm = norm(candidate)
    for name in known:
        if norm(name) == c_norm:
            return name
    return None


# ── Value resolver ─────────────────────────────────────────────────────────────

def _resolve(old: float, chg: ScenarioChange) -> float:
    if chg.value is not None:
        return float(chg.value)
    if chg.delta_abs is not None:
        return old + float(chg.delta_abs)
    if chg.delta_pct is not None:
        return old * (1.0 + chg.delta_pct / 100.0)
    return old


# ── Main entry point ──────────────────────────────────────────────────────────

_KNOWN_CHANGE_TYPES = {
    "overtime", "working_hours", "working_days",
    "availability", "payload",
    "speed_loaded", "speed_empty", "speed_both",
    "terminal_capacity", "unload_time", "cp_capacity",
    "demand", "cost_variable", "cost_fixed", "cost_overtime",
}


def apply_scenario(pre: PreprocessedData, changes: list[ScenarioChange]) -> PreprocessedData:
    """Return a deep copy of *pre* with all *changes* applied.

    Derived arrays (cycle_time, window_feasible, demand_mask, daily_demand)
    are recomputed automatically when their inputs change.

    Raises ValueError if any change uses an unrecognised change_type.
    """
    unknown = [c.change_type for c in changes if c.change_type not in _KNOWN_CHANGE_TYPES]
    if unknown:
        raise ValueError(f"Unknown change_type(s): {', '.join(dict.fromkeys(unknown))}")

    p = copy.deepcopy(pre)

    _need_cycle = False
    _need_feasibility = False

    for chg in changes:
        ct = chg.change_type

        # ── Driver policy ─────────────────────────────────────────────────────
        if ct == "overtime":
            p.overtime_hours = max(0.0, _resolve(p.overtime_hours, chg))
            p.effective_hours = p.working_hours_per_day - p.lunch_hours + p.overtime_hours

        elif ct == "working_hours":
            p.working_hours_per_day = max(1.0, _resolve(p.working_hours_per_day, chg))
            p.effective_hours = p.working_hours_per_day - p.lunch_hours + p.overtime_hours

        elif ct == "working_days":
            p.working_days = max(1, int(round(_resolve(float(p.working_days), chg))))
            p.daily_demand = p.monthly_demand / p.working_days

        # ── Truck parameters ─────────────────────────────────────────────────
        elif ct == "availability":
            p.availability = max(0.01, min(1.0, _resolve(p.availability, chg)))

        elif ct == "payload":
            p.payload = max(0.1, _resolve(p.payload, chg))

        elif ct == "speed_loaded":
            p.speed_loaded = max(1.0, _resolve(p.speed_loaded, chg))
            p.drive_time_loaded = p.dist_cp_terminal / p.speed_loaded
            _need_cycle = True

        elif ct == "speed_empty":
            p.speed_empty = max(1.0, _resolve(p.speed_empty, chg))
            p.drive_time_empty = (p.dist_cp_terminal / p.speed_empty).T
            p.drive_time_cp_cp = p.dist_cp_cp / p.speed_empty
            _need_cycle = True

        elif ct == "speed_both":
            if chg.speed_loaded is not None:
                p.speed_loaded = max(1.0, float(chg.speed_loaded))
                p.drive_time_loaded = p.dist_cp_terminal / p.speed_loaded
            if chg.speed_empty is not None:
                p.speed_empty = max(1.0, float(chg.speed_empty))
                p.drive_time_empty = (p.dist_cp_terminal / p.speed_empty).T
                p.drive_time_cp_cp = p.dist_cp_cp / p.speed_empty
            _need_cycle = True

        # ── Terminal changes ──────────────────────────────────────────────────
        elif ct == "terminal_capacity":
            targets = chg.terminal_targets or chg.targets or p.terminal_names
            for raw_name in targets:
                name = _match_name(raw_name, p.terminal_names)
                if name is not None:
                    j = p.terminal_names.index(name)
                    p.terminal_capacity_monthly[j] = max(
                        0.0, _resolve(p.terminal_capacity_monthly[j], chg)
                    )

        elif ct == "unload_time":
            targets = chg.terminal_targets or chg.targets or p.terminal_names
            for raw_name in targets:
                name = _match_name(raw_name, p.terminal_names)
                if name is not None:
                    j = p.terminal_names.index(name)
                    old_ut = p.t_unload_times[j]
                    new_ut = max(0.0, _resolve(old_ut, chg))
                    p.cycle_time[:, j] += (new_ut - old_ut)
                    p.t_unload_times[j] = new_ut
            _need_feasibility = True

        # ── CP changes ────────────────────────────────────────────────────────
        elif ct == "cp_capacity":
            targets = chg.targets or p.cp_names
            for raw_name in targets:
                name = _match_name(raw_name, p.cp_names)
                if name is not None:
                    i = p.cp_names.index(name)
                    p.cp_capacity_monthly[i] = max(
                        0.0, _resolve(p.cp_capacity_monthly[i], chg)
                    )

        # ── Cost changes ──────────────────────────────────────────────────────
        elif ct == "cost_variable":
            comp = chg.component_name or ""
            comp_norm = comp.lower().replace(" ", "").replace(",", "")
            items = p.cost_breakdown["variable"]
            matched = False
            for idx, (name, rate) in enumerate(items):
                name_norm = name.lower().replace(" ", "").replace(",", "")
                if name_norm == comp_norm:
                    items[idx] = (name, max(0.0, _resolve(rate, chg)))
                    matched = True
                    break
            if matched:
                p.variable_cost_per_km = sum(v for _, v in items)

        elif ct == "cost_fixed":
            comp = chg.component_name or ""
            comp_norm = comp.lower().replace(" ", "").replace(",", "")
            items = p.cost_breakdown["fixed"]
            matched = False
            for idx, (name, cost) in enumerate(items):
                name_norm = name.lower().replace(" ", "").replace(",", "")
                if name_norm == comp_norm:
                    items[idx] = (name, max(0.0, _resolve(cost, chg)))
                    matched = True
                    break
            if matched:
                p.fixed_cost_per_truck_month = sum(v for _, v in items)

        elif ct == "cost_overtime":
            p.cost_overtime_driver = max(0.0, _resolve(p.cost_overtime_driver, chg))

        # ── Demand changes ────────────────────────────────────────────────────
        elif ct == "demand":
            cp_targets = chg.targets or p.cp_names
            t_targets = chg.terminal_targets or p.terminal_names
            explicit_terminal = chg.terminal_targets is not None
            for raw_cp in cp_targets:
                cp_name = _match_name(raw_cp, p.cp_names)
                if cp_name is not None:
                    i = p.cp_names.index(cp_name)
                    for raw_t in t_targets:
                        t_name = _match_name(raw_t, p.terminal_names)
                        if t_name is not None:
                            j = p.terminal_names.index(t_name)
                            # Allow update if lane has existing demand, or if the
                            # terminal was explicitly targeted (lane-specific change)
                            if p.monthly_demand[i, j] > 0 or explicit_terminal:
                                p.monthly_demand[i, j] = max(
                                    0.0, _resolve(p.monthly_demand[i, j], chg)
                                )
            p.daily_demand = p.monthly_demand / p.working_days
            _need_feasibility = True

    # ── Recompute derived arrays ──────────────────────────────────────────────
    if _need_cycle:
        p.cycle_time = (
            p.cp_load_times[:, np.newaxis]
            + p.drive_time_loaded
            + p.t_unload_times[np.newaxis, :]
            + p.drive_time_empty.T
        )
        _need_feasibility = True

    if _need_feasibility:
        p.demand_mask = p.monthly_demand > 0
        p.window_feasible = (p.cycle_time < p.effective_window) & p.demand_mask

    return p
