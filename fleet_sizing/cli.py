"""CLI presentation layer for Fleet Sizing Optimizer.

All display logic lives here. No business logic — functions only accept data
structures and render them to the terminal.
"""
from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from .data import PreprocessedData
from .i18n import t
from .results import Scope41Result, Scope42Result, Scope43Result


def _component_name(name: str) -> str:
    """Return the localized display name for a cost component, falling back to the raw name."""
    key = f"cli.costs.component.{name}"
    result = t(key)
    return result if result != key else name


def _display_terminal_name(name: str) -> str:
    """Unload_Terminal_A → Terminal A  (display-only; data identifier unchanged)."""
    if name.startswith("Unload_Terminal_"):
        return "Terminal " + name[len("Unload_Terminal_"):]
    return name


def _display_cp_name(name: str) -> str:
    """Collection_Point01 → CP 01 (en) or PC 01 (pt_BR)  (display-only)."""
    import re
    m = re.search(r"\d+$", name)
    if "Collection_Point" in name and m:
        return t("cli.nodes.cp_display_prefix") + " " + m.group()
    return name

console = Console()

ScopeTriple = tuple[Scope41Result, Scope42Result, Scope43Result]


# ── Welcome / entry ──────────────────────────────────────────────────────────

def show_welcome() -> None:
    console.print()
    console.print(Panel.fit(
        t("cli.welcome.body"),
        border_style="cyan",
    ))
    console.print()


def confirm_start() -> bool:
    return Confirm.ask(
        t("cli.confirm_start"),
        default=True,
    )


# ── Data loading ─────────────────────────────────────────────────────────────

def show_loading_data() -> None:
    console.print(t("cli.loading_data"))


def show_problem_summary(pre: PreprocessedData) -> None:
    console.print(t("cli.summary.title"))

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()

    table.add_row(t("cli.summary.cp"), f"{len(pre.cp_names)}")
    table.add_row(t("cli.summary.terminals"), f"{len(pre.terminal_names)}")
    table.add_row(t("cli.summary.demand_label"), t("cli.summary.demand_value", total=pre.monthly_demand.sum()))
    table.add_row(t("cli.summary.working_days_label"), f"{pre.working_days}")
    table.add_row(t("cli.summary.payload_label"), t("cli.summary.payload_value", payload=pre.payload))
    table.add_row(t("cli.summary.eff_hours_label"), t("cli.summary.eff_hours_value", hours=pre.effective_hours))
    table.add_row(t("cli.summary.speed_label"), t("cli.summary.speed_value", loaded=pre.speed_loaded, empty=pre.speed_empty))

    console.print(table)
    console.print()

    n_cp = len(pre.cp_names)
    n_t = len(pre.terminal_names)
    if n_cp > 30 or n_t > 5:
        console.print(t("cli.summary.scale_warning", n_cp=n_cp, n_t=n_t))
        console.print()


# ── Baseline ─────────────────────────────────────────────────────────────────

def confirm_run_baseline() -> bool:
    return Confirm.ask(
        t("cli.confirm_baseline"),
        default=True,
    )


def show_scope_progress(scope_label: str) -> None:
    console.print(t("cli.scope_progress", label=scope_label))



def _show_milp_capacity_violations(r43: Scope43Result) -> None:
    """Render a warning panel listing nodes whose demand exceeds their monthly capacity."""
    violations = r43.capacity_violations
    n = len(violations)
    console.print(t("cli.milp.infeasible_header", n=n))
    console.print(t("cli.milp.infeasible_body"))
    for v in violations:
        key = "cli.milp.infeasible_row_cp" if v.node_type == "cp" else "cli.milp.infeasible_row_terminal"
        console.print(t(key, name=v.node_name, demand=v.demand, cap=v.capacity, over=v.demand - v.capacity))
    console.print(t("cli.milp.infeasible_hint"))
    console.print()


def show_baseline_results(r41: Scope41Result, r42: Scope42Result, r43: Scope43Result) -> None:
    console.print(t("cli.baseline.complete"))

    table = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    table.add_column(t("cli.baseline.col_scope"), style="cyan")
    table.add_column(t("cli.baseline.col_method"))
    table.add_column(t("cli.baseline.col_fleet"), justify="right")
    table.add_column(t("cli.baseline.col_trips"), justify="right")
    table.add_column(t("cli.baseline.col_trips_rate"), justify="right")
    table.add_column(t("cli.baseline.col_cost"), justify="right")
    table.add_column(t("cli.baseline.col_cost_ton"), justify="right")

    def _cost_row(r) -> tuple[str, str]:
        if r.monthly_operational_cost_usd > 0 and r.total_monthly_volume_tons > 0:
            cost_str = f"${r.monthly_operational_cost_usd:>10,.0f}"
            per_ton  = r.monthly_operational_cost_usd / r.total_monthly_volume_tons
            return cost_str, f"${per_ton:.2f}"
        return "—", "—"

    c41, t41 = _cost_row(r41)
    c42, t42 = _cost_row(r42)
    c43, t43 = _cost_row(r43)

    table.add_row(
        "4.1", t("cli.baseline.method_41"),
        str(r41.total_trucks),
        f"{r41.total_trips_month:,}",
        f"{r41.trips_per_truck_per_day:.2f}",
        c41, t41,
    )
    table.add_row(
        "4.2", t("cli.baseline.method_42"),
        str(r42.total_trucks),
        f"{r42.total_trips_month:,}",
        f"{r42.trips_per_truck_per_day:.2f}",
        c42, t42,
    )
    milp_fleet = (
        f"[bold yellow]{t('cli.milp.infeasible_cell')}[/bold yellow]"
        if r43.capacity_violations
        else f"[bold]{r43.total_trucks}[/bold]"
    )
    table.add_row(
        "4.3", t("cli.baseline.method_43"),
        milp_fleet,
        f"{r43.total_trips_month:,}",
        f"{r43.trips_per_truck_per_day:.2f}",
        f"[bold]{c43}[/bold]", f"[bold]{t43}[/bold]",
    )

    console.print(table)

    if r43.capacity_violations:
        _show_milp_capacity_violations(r43)

    infeasible = [l for l in r41.lane_results if "infeasible" in l.notes]
    if infeasible:
        n = len(infeasible)
        key = "cli.baseline.infeasible_plural" if n != 1 else "cli.baseline.infeasible_singular"
        console.print(t(key, n=n))
    console.print()


# ── Comparison (baseline vs scenario) ────────────────────────────────────────


def show_comparison(
    baseline: ScopeTriple,
    scenario: ScopeTriple,
    description: str,
) -> None:
    """Render a side-by-side Baseline | Scenario | Change table."""
    b41, b42, b43 = baseline
    s41, s42, s43 = scenario

    console.print(Panel.fit(
        f"{t('cli.comparison.panel_header')}\n\n{description}",
        border_style="cyan",
    ))
    console.print()

    # Fleet size
    console.print(t("cli.comparison.fleet_header"))
    fleet_t = _comparison_table()
    for label, bv, sv in [
        (t("cli.scope.41_short"), b41.total_trucks, s41.total_trucks),
        (t("cli.scope.42_short"), b42.total_trucks, s42.total_trucks),
        (t("cli.scope.43_short"), b43.total_trucks, s43.total_trucks),
    ]:
        fleet_t.add_row(label, str(bv), str(sv), _delta_int(bv, sv))
    console.print(fleet_t)

    # Monthly operational cost
    console.print(t("cli.comparison.cost_header"))
    cost_t = _comparison_table()
    for label, bv, sv in [
        (t("cli.scope.41_short"), b41.monthly_operational_cost_usd, s41.monthly_operational_cost_usd),
        (t("cli.scope.42_short"), b42.monthly_operational_cost_usd, s42.monthly_operational_cost_usd),
        (t("cli.scope.43_short"), b43.monthly_operational_cost_usd, s43.monthly_operational_cost_usd),
    ]:
        cost_t.add_row(
            label,
            f"${bv:,.0f}",
            f"${sv:,.0f}",
            _delta_cost(bv, sv),
        )
    console.print(cost_t)
    console.print(t("cli.comparison.cost_note"))

    # Warn about routes that became infeasible under this scenario
    b_infeasible = {l.cp_name + l.terminal_name for l in b41.lane_results if "infeasible" in l.notes}
    s_infeasible = {l.cp_name + l.terminal_name for l in s41.lane_results if "infeasible" in l.notes}
    newly_infeasible = s_infeasible - b_infeasible
    if newly_infeasible:
        count = len(newly_infeasible)
        key = "cli.comparison.infeasible_plural" if count != 1 else "cli.comparison.infeasible_singular"
        console.print(t(key, n=count))

    if s43.capacity_violations:
        _show_milp_capacity_violations(s43)

    console.print()


def _comparison_table() -> Table:
    tbl = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    tbl.add_column(t("cli.comparison.col_scope"), style="cyan")
    tbl.add_column(t("cli.comparison.col_baseline"), justify="right")
    tbl.add_column(t("cli.comparison.col_scenario"), justify="right")
    tbl.add_column(t("cli.comparison.col_change"), justify="right")
    return tbl


def _delta_cell(diff: float, value_str: str, pct: float) -> str:
    sign = "+" if diff > 0 else ""
    color = "green" if diff < 0 else "red"
    return f"[{color}]{sign}{value_str} ({sign}{pct:.1f}%)[/{color}]"


def _delta_int(bv: int, sv: int) -> str:
    diff = sv - bv
    if diff == 0:
        return "[dim]—[/dim]"
    return _delta_cell(diff, str(diff), diff / bv * 100 if bv else 0.0)


def _delta_cost(bv: float, sv: float) -> str:
    diff = sv - bv
    if abs(diff) < 1:
        return "[dim]—[/dim]"
    return _delta_cell(diff, f"${diff:,.0f}", diff / bv * 100 if bv else 0.0)



# ── Error / cancel ────────────────────────────────────────────────────────────

def show_error(error_message: str) -> None:
    console.print(f"\n[bold red]{t('cli.error_prefix')}[/bold red] {error_message}\n")


def show_cancellation() -> None:
    console.print(t("cli.cancelled"))


# ── Informational displays (commands) ────────────────────────────────────────

def show_driver_policy(pre: PreprocessedData) -> None:
    console.print(t("cli.driver.title"))

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()

    table.add_row(t("cli.driver.shift"), t("cli.driver.shift_value", h=pre.working_hours_per_day))
    table.add_row(t("cli.driver.lunch"), t("cli.driver.lunch_value", h=pre.lunch_hours))
    table.add_row(t("cli.driver.overtime"), t("cli.driver.overtime_value", h=pre.overtime_hours))
    table.add_row(t("cli.driver.eff_hours"), t("cli.driver.eff_hours_value", h=pre.effective_hours))
    table.add_row(t("cli.driver.working_days"), t("cli.driver.working_days_value", n=pre.working_days))
    table.add_row(t("cli.driver.payload"), t("cli.driver.payload_value", t=pre.payload))
    table.add_row(t("cli.driver.speed_loaded"), t("cli.driver.speed_value", s=pre.speed_loaded))
    table.add_row(t("cli.driver.speed_empty"), t("cli.driver.speed_value", s=pre.speed_empty))
    table.add_row(t("cli.driver.availability"), f"{pre.availability:.0%}")

    console.print(table)
    console.print()


def show_nodes_list(pre: PreprocessedData) -> None:
    console.print(t("cli.nodes.title"))

    # ── Collection Points ─────────────────────────────────────────────────────
    cp_table = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    cp_table.add_column(t("cli.nodes.col_num"), justify="right", style="dim")
    cp_table.add_column(t("cli.nodes.col_cp"), style="cyan")
    cp_table.add_column(t("cli.nodes.col_capacity"), justify="right")
    for t_name in pre.terminal_names:
        cp_table.add_column(f"→ {_display_terminal_name(t_name)}", justify="right")
    cp_table.add_column(t("cli.nodes.col_demand_total"), justify="right")
    cp_table.add_column(t("cli.nodes.col_utilization"), justify="right")

    for i, name in enumerate(pre.cp_names):
        cap = pre.cp_capacity_monthly[i]
        demands = [pre.monthly_demand[i, j] for j in range(len(pre.terminal_names))]
        total_demand = sum(demands)
        util = total_demand / cap * 100 if cap > 0 else 0.0
        util_color = "green" if util <= 80 else "yellow" if util <= 100 else "red"
        cp_table.add_row(
            str(i + 1),
            _display_cp_name(name),
            f"{cap:,.0f}",
            *[f"{d:,.0f}" for d in demands],
            f"{total_demand:,.0f}",
            f"[{util_color}]{util:.1f}%[/{util_color}]",
        )

    console.print(t("cli.nodes.cp_header"))
    console.print(cp_table)
    console.print()

    # ── Unload Terminals ──────────────────────────────────────────────────────
    t_table = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    t_table.add_column(t("cli.nodes.col_num"), justify="right", style="dim")
    t_table.add_column(t("cli.nodes.col_terminal"), style="cyan")
    t_table.add_column(t("cli.nodes.col_capacity"), justify="right")
    t_table.add_column(t("cli.nodes.col_demand_total"), justify="right")
    t_table.add_column(t("cli.nodes.col_utilization"), justify="right")
    t_table.add_column(t("cli.nodes.col_unload_time"), justify="right")

    for j, name in enumerate(pre.terminal_names):
        cap = pre.terminal_capacity_monthly[j]
        ut = pre.t_unload_times[j]
        total_demand = float(pre.monthly_demand[:, j].sum())
        util = total_demand / cap * 100 if cap > 0 else 0.0
        util_color = "green" if util <= 80 else "yellow" if util <= 100 else "red"
        t_table.add_row(
            str(j + 1),
            _display_terminal_name(name),
            f"{cap:,.0f}",
            f"{total_demand:,.0f}",
            f"[{util_color}]{util:.1f}%[/{util_color}]",
            f"{ut:.2f}",
        )

    console.print(t("cli.nodes.terminal_header"))
    console.print(t_table)
    console.print()


def show_relocation_result(result: "RelocationResult") -> None:  # noqa: F821
    """Display the optimal demand relocation table and estimated savings."""
    console.print()
    console.print(Panel.fit(
        t("cli.relocation.panel_title"),
        border_style="cyan",
    ))
    console.print()

    if not result.lane_changes:
        console.print(t("cli.relocation.already_optimal"))
        return

    tbl = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    tbl.add_column(t("cli.relocation.col_cp"), style="cyan")
    tbl.add_column(t("cli.relocation.col_terminal"))
    tbl.add_column(t("cli.relocation.col_before"), justify="right")
    tbl.add_column(t("cli.relocation.col_after"),  justify="right")
    tbl.add_column(t("cli.relocation.col_change"), justify="right")

    for r in result.lane_changes:
        sign  = "+" if r.delta > 0 else ""
        color = "red" if r.delta > 0 else "green"
        tbl.add_row(
            _display_cp_name(r.cp_name),
            _display_terminal_name(r.terminal_name),
            f"{r.demand_before:,.0f}",
            f"{r.demand_after:,.0f}",
            f"[{color}]{sign}{r.delta:,.0f}[/{color}]",
        )

    console.print(tbl)
    console.print(t("cli.relocation.saving", saving=result.saving, pct=result.saving_pct))


def show_operational_costs(pre: PreprocessedData) -> None:
    console.print(t("cli.costs.title"))

    # ── Variable costs ($/km) ─────────────────────────────────────────────────
    console.print(t("cli.costs.variable_header"))
    var_t = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    var_t.add_column(t("cli.costs.col_component"), style="cyan")
    var_t.add_column(t("cli.costs.col_variable"), justify="right")

    for name, rate in pre.cost_breakdown["variable"]:
        var_t.add_row(_component_name(name), f"{rate:.4f}")

    var_t.add_section()
    var_t.add_row(t("cli.costs.variable_total"), f"[bold]{pre.variable_cost_per_km:.4f}[/bold]")
    console.print(var_t)

    # ── Fixed costs ($/truck/month) ───────────────────────────────────────────
    console.print(t("cli.costs.fixed_header"))
    fix_t = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    fix_t.add_column(t("cli.costs.col_component"), style="cyan")
    fix_t.add_column(t("cli.costs.col_fixed"), justify="right")

    for name, cost in pre.cost_breakdown["fixed"]:
        fix_t.add_row(_component_name(name), f"{cost:,.2f}")

    fix_t.add_section()
    fix_t.add_row(t("cli.costs.fixed_total"), f"[bold]{pre.fixed_cost_per_truck_month:,.2f}[/bold]")
    console.print(fix_t)

    # ── Overtime rate ─────────────────────────────────────────────────────────
    console.print(t("cli.costs.overtime_header"))
    ot_t = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    ot_t.add_column(t("cli.costs.col_component"), style="cyan")
    ot_t.add_column(t("cli.costs.col_overtime"), justify="right")
    ot_t.add_row(t("cli.costs.overtime_driver"), f"{pre.cost_overtime_driver:,.2f}")
    console.print(ot_t)

    console.print()


def show_whatif_types(pre: PreprocessedData) -> None:
    console.print(t("cli.whatif.title"))

    _section(t("cli.whatif.s1_title"), t("cli.whatif.s1_subtitle"))
    console.print(t("cli.whatif.s1_ex1"))
    console.print(t("cli.whatif.s1_ex2"))
    console.print(t("cli.whatif.s1_ex3"))
    console.print()

    _section(t("cli.whatif.s2_title"), t("cli.whatif.s2_subtitle"))
    console.print(t("cli.whatif.s2_ex1"))
    console.print(t("cli.whatif.s2_ex2"))
    console.print(t("cli.whatif.s2_ex3"))
    console.print()

    _section(t("cli.whatif.s3_title"), t("cli.whatif.s3_subtitle"))
    console.print(t("cli.whatif.s3_ex1"))
    console.print(t("cli.whatif.s3_ex2"))
    console.print(t("cli.whatif.s3_ex3"))
    console.print()

    _section(t("cli.whatif.s4_title"), t("cli.whatif.s4_subtitle"))
    console.print(t("cli.whatif.s4_ex1"))
    console.print(t("cli.whatif.s4_ex2"))
    console.print(t("cli.whatif.s4_ex3"))
    console.print()

    _section(t("cli.whatif.s5_title"), t("cli.whatif.s5_subtitle"))
    console.print(t("cli.whatif.s5_body1"))
    console.print(t("cli.whatif.s5_body2"))
    console.print(t("cli.whatif.s5_body3"))
    console.print()
    console.print(t("cli.whatif.s5_ex1"))
    console.print(t("cli.whatif.s5_ex2"))
    console.print(t("cli.whatif.s5_ex3"))
    console.print()

    console.print(t("cli.whatif.tips_title"))
    console.print(t("cli.whatif.tip1"))
    console.print(t("cli.whatif.tip2"))
    console.print(t("cli.whatif.tip3"))
    console.print(t("cli.whatif.tip4"))
    console.print()


def show_planning_analysis(report: str) -> None:
    """Display the planning analyst's strategic report in the terminal."""
    console.print()
    console.print(Panel.fit(
        t("cli.analyst.panel_title"),
        border_style="cyan",
    ))
    console.print()
    for line in report.splitlines():
        # Section headers (ALL CAPS lines with at least one letter) get bold styling
        stripped = line.strip()
        if stripped and stripped == stripped.upper() and any(c.isalpha() for c in stripped) and len(stripped) > 3:
            console.print(f"[bold]{stripped}[/bold]")
        else:
            console.print(f"  {line}" if line.strip() else "")
    console.print()


def show_baseline_detail(baseline_results: ScopeTriple) -> None:
    """Display the baseline fleet sizing results panel."""
    r41, r42, r43 = baseline_results
    console.print()
    console.print(Panel.fit(
        t("cli.baseline_detail.panel_title"),
        border_style="cyan",
    ))
    console.print()
    console.print(t("cli.baseline_detail.row_41", trucks=r41.total_trucks, cost=r41.monthly_operational_cost_usd))
    console.print(t("cli.baseline_detail.row_42", trucks=r42.total_trucks, cost=r42.monthly_operational_cost_usd))
    console.print(t("cli.baseline_detail.row_43", trucks=r43.total_trucks, cost=r43.monthly_operational_cost_usd))

    vol = r42.total_monthly_volume_tons  # same across all scopes
    cpt41 = r41.monthly_operational_cost_usd / r41.total_trucks if r41.total_trucks else 0
    cpt42 = r42.monthly_operational_cost_usd / r42.total_trucks if r42.total_trucks else 0
    cpt43 = r43.monthly_operational_cost_usd / r43.total_trucks if r43.total_trucks else 0
    cts41 = r41.monthly_operational_cost_usd / vol if vol else 0
    cts42 = r42.monthly_operational_cost_usd / vol if vol else 0
    cts43 = r43.monthly_operational_cost_usd / vol if vol else 0
    console.print()
    console.print(t("cli.baseline_detail.cost_per_truck", c41=cpt41, c42=cpt42, c43=cpt43))
    console.print(t("cli.baseline_detail.cost_per_ton", c41=cts41, c42=cts42, c43=cts43))

    infeasible = [l for l in r41.lane_results if "infeasible" in l.notes]
    if infeasible:
        n = len(infeasible)
        key = "cli.baseline.infeasible_plural" if n != 1 else "cli.baseline.infeasible_singular"
        console.print(t(key, n=n))


def show_network(pre: PreprocessedData) -> None:
    """Render an ASCII bipartite topology sketch and demand heatmap for the current state."""
    import numpy as np

    n_cp = len(pre.cp_names)
    n_t = len(pre.terminal_names)
    demand = pre.monthly_demand  # shape (n_cp, n_t)

    total_demand = demand.sum()
    cp_totals = demand.sum(axis=1)       # per CP
    t_totals = demand.sum(axis=0)        # per terminal
    order = cp_totals.argsort()[::-1]    # sort CPs by total demand descending

    # Percentile thresholds for cell coloring (non-zero values only)
    nonzero = demand[demand > 0]
    p75 = float(np.percentile(nonzero, 75)) if len(nonzero) else 1.0
    p25 = float(np.percentile(nonzero, 25)) if len(nonzero) else 0.0

    def _demand_color(val: float) -> str:
        if val <= 0:
            return "dim"
        if val >= p75:
            return "red"
        if val >= p25:
            return "yellow"
        return "green"

    console.print()
    console.print(Panel.fit(
        t("cli.network.panel_title", n_cp=n_cp, n_t=n_t),
        border_style="cyan",
    ))
    console.print()

    # ── ASCII topology sketch ─────────────────────────────────────────────────
    console.print(t("cli.network.topology_header"))
    console.print()

    # Group CPs by dominant terminal (highest demand)
    groups: dict[int, list[int]] = {j: [] for j in range(n_t)}
    dual: list[int] = []
    for i in order:
        row = demand[i]
        active = [j for j in range(n_t) if row[j] > 0]
        if len(active) == 0:
            continue
        if len(active) == 1:
            groups[active[0]].append(i)
        else:
            dual.append(i)

    for j, t_name in enumerate(pre.terminal_names):
        t_display = _display_terminal_name(t_name)
        cps_in_group = groups[j]
        # also include dual-homed CPs that have this terminal as dominant
        for i in dual:
            if demand[i].argmax() == j:
                cps_in_group = [i] + cps_in_group

        if not cps_in_group:
            continue

        for k, i in enumerate(cps_in_group):
            cp_display = _display_cp_name(pre.cp_names[i])
            is_last = k == len(cps_in_group) - 1

            if len(cps_in_group) == 1:
                connector = "────"
            elif k == 0:
                connector = "──┐ "
            elif is_last:
                connector = "──┘ "
            else:
                connector = "──┤ "

            if k == len(cps_in_group) // 2:
                arrow = f"──► [bold]{t_display}[/bold]  [dim]{t_totals[j]:,.0f} t/mo[/dim]"
            else:
                arrow = ""

            console.print(
                f"  [cyan]{cp_display:<10}[/cyan] ({cp_totals[i]:>7,.0f} t)  {connector}{arrow}"
            )
        console.print()

    # ── Demand heatmap table ──────────────────────────────────────────────────
    console.print(t("cli.network.heatmap_header"))
    tbl = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 2))
    tbl.add_column(t("cli.network.col_cp"), style="cyan")
    for t_name in pre.terminal_names:
        tbl.add_column(f"→ {_display_terminal_name(t_name)}", justify="right")
    tbl.add_column(t("cli.network.col_total"), justify="right")
    tbl.add_column(t("cli.network.col_share"), justify="right")

    for i in order:
        cp_display = _display_cp_name(pre.cp_names[i])
        cells = []
        for j in range(n_t):
            val = demand[i, j]
            color = _demand_color(val)
            cells.append(f"[{color}]{val:>8,.0f}[/{color}]" if val > 0 else "[dim]    —[/dim]")
        total = cp_totals[i]
        share = total / total_demand * 100 if total_demand > 0 else 0.0
        tbl.add_row(cp_display, *cells, f"{total:>8,.0f}", f"{share:.1f}%")

    tbl.add_section()
    tbl.add_row(
        t("cli.network.row_total"),
        *[f"[bold]{t_totals[j]:>8,.0f}[/bold]" for j in range(n_t)],
        f"[bold]{total_demand:>8,.0f}[/bold]",
        "[bold]100.0%[/bold]",
    )
    console.print(tbl)

    # ── Summary footer ────────────────────────────────────────────────────────
    console.print()
    if n_t == 2:
        pct_a = t_totals[0] / total_demand * 100 if total_demand else 0
        pct_b = t_totals[1] / total_demand * 100 if total_demand else 0
        imbalance = abs(pct_a - pct_b)
        ta = _display_terminal_name(pre.terminal_names[0])
        tb = _display_terminal_name(pre.terminal_names[1])
        console.print(t(
            "cli.network.summary_2t",
            ta=ta, pct_a=pct_a, tb=tb, pct_b=pct_b, imbalance=imbalance,
        ))
    elif n_t > 2:
        pcts = [t_totals[j] / total_demand * 100 if total_demand else 0 for j in range(n_t)]
        for j in range(n_t):
            console.print(t(
                "cli.network.summary_terminal_pct",
                terminal=_display_terminal_name(pre.terminal_names[j]),
                pct=pcts[j],
            ))
        console.print(t("cli.network.summary_spread_nt", spread=max(pcts) - min(pcts)))

    busiest_idx = int(cp_totals.argmax())
    busiest_cp = _display_cp_name(pre.cp_names[busiest_idx])
    busiest_t_idx = int(demand[busiest_idx].argmax())
    busiest_t = _display_terminal_name(pre.terminal_names[busiest_t_idx])
    console.print(t(
        "cli.network.summary_busiest",
        cp=busiest_cp, terminal=busiest_t, demand=demand[busiest_idx, busiest_t_idx],
    ))
    console.print(t("cli.network.color_legend"))
    console.print()


def _section(title: str, subtitle: str) -> None:
    console.print(t("cli.whatif.section_divider"))
    console.print(f"[bold]{title}[/bold]")
    console.print(f"[dim]{subtitle}[/dim]")
    console.print()
    console.print(t("cli.whatif.section_examples"))
