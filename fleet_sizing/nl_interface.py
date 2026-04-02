"""Natural language what-if interface powered by the Claude API.

The user types plain-English questions at the prompt.  Each question is sent
to Claude (haiku model for speed), which returns a JSON object describing
which model parameters should change and by how much.  The changes are applied
to a deep copy of PreprocessedData and all three scopes are re-solved.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings
from rich.panel import Panel
from rich.prompt import Confirm

from .cli import (
    ScopeTriple,
    console,
    show_baseline_detail,
    show_network,
    show_planning_analysis,
    show_comparison,
    show_driver_policy,
    show_error,
    show_nodes_list,
    show_operational_costs,
    show_relocation_result,
    show_whatif_types,
)
from .data import PreprocessedData
from .i18n import t, set_language, current_language, AVAILABLE_LANGS
from .report import write_csv
from .scenario import ScenarioChange, apply_scenario
from . import scope_41, scope_42, scope_43

_OUTPUTS_BASE = Path(__file__).parent.parent / "outputs"

# ── Language-aware command lists ──────────────────────────────────────────────

_LANG_COMMANDS: dict[str, list[str]] = {
    "en": [
        "/list", "/driver", "/whatif", "/relocate", "/baseline", "/onboarding",
        "/operational-costs", "/planning-analyst", "/network", "/language", "/help", "/clear", "/quit",
    ],
    "pt_BR": [
        "/listar", "/motorista", "/cenarios", "/realocar", "/base", "/introducao",
        "/custos-operacionais", "/analista-planejamento", "/rede", "/idioma", "/ajuda", "/limpar", "/sair",
    ],
}

# Alias map: localized command → canonical English command (for handler dispatch)
_CMD_ALIASES: dict[str, str] = {
    "/listar": "/list",
    "/motorista": "/driver",
    "/cenarios": "/whatif",
    "/realocar": "/relocate",
    "/base": "/baseline",
    "/introducao": "/onboarding",
    "/custos-operacionais": "/operational-costs",
    "/analista-planejamento": "/planning-analyst",
    "/rede": "/network",
    "/idioma": "/language",
    "/ajuda": "/help",
    "/limpar": "/clear",
    "/sair": "/quit",
}


def _get_commands_for_lang(lang: str) -> list[str]:
    return _LANG_COMMANDS.get(lang, _LANG_COMMANDS["en"])


def _ask_confirm(prompt_text: str, default: bool = True) -> bool:
    """Locale-aware yes/no prompt. Shows [s/n] in Portuguese, [y/n] in English."""
    if current_language() != "pt_BR":
        return Confirm.ask(prompt_text, default=default)
    default_str = "s" if default else "n"
    raw = console.input(f"{prompt_text} \\[s/n]: ").strip().lower()
    if not raw:
        return default
    return raw in ("s", "sim", "y", "yes")


# One-keypress key bindings for the post-report rating prompt
_RATING_BINDINGS = KeyBindings()

@_RATING_BINDINGS.add("1")
@_RATING_BINDINGS.add("2")
@_RATING_BINDINGS.add("3")
def _submit_rating(event):
    event.app.current_buffer.text = event.key_sequence[0].key
    event.app.current_buffer.validate_and_handle()


# ── Solver helper ─────────────────────────────────────────────────────────────

def _run_all_solvers(pre: PreprocessedData) -> ScopeTriple:
    console.print(t("nl.solver.41"))
    s41 = scope_41.solve(pre)
    console.print(t("nl.solver.42"))
    s42 = scope_42.solve(pre)
    console.print(t("nl.solver.43"))
    s43 = scope_43.solve(pre, verbose=False)
    return s41, s42, s43


# ── Public entry point ────────────────────────────────────────────────────────

def run_interactive_whatif(
    pre: PreprocessedData,
) -> None:
    """Interactive what-if loop.  Returns when the user types quit/exit."""

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print(t("nl.api_key.missing"))
        console.print(t("nl.api_key.set_key"))
        console.print(t("nl.api_key.windows"))
        console.print(t("nl.api_key.linux"))
        console.print(t("nl.api_key.get"))
        return

    _OUTPUTS_BASE.mkdir(parents=True, exist_ok=True)
    sim_dir = _OUTPUTS_BASE / f"simulation_{datetime.now().strftime('%H%M_%d%m')}"

    baseline_results: ScopeTriple | None = None
    current_pre: PreprocessedData = pre  # tracks the most recently solved state
    scenario_count = 0

    commands = _get_commands_for_lang(current_language())
    completer = WordCompleter(commands, pattern=r"/\w*", sentence=True)
    session: PromptSession = PromptSession(completer=completer, complete_while_typing=True)

    while True:
        try:
            console.print()
            user_query = session.prompt(t("nl.prompt"))

            q = user_query.strip().lower()

            # Normalize locale-specific aliases to canonical English commands
            for _alias, _canonical in _CMD_ALIASES.items():
                if q == _alias or q.startswith(_alias + " "):
                    q = _canonical + q[len(_alias):]
                    break

            # ── Built-in commands ─────────────────────────────────────────────
            if q in ("/quit", "quit", "exit", "q"):
                break

            if q in ("/help", "help", "h", "?"):
                _display_help()
                continue

            if q == "/list":
                show_nodes_list(pre)
                continue

            if q == "/driver":
                show_driver_policy(pre)
                continue

            if q == "/whatif":
                show_whatif_types(pre)
                continue

            if q == "/relocate":
                if baseline_results is None:
                    console.print(t("nl.baseline_required"))
                    continue
                _, relocated_pre = _run_relocate(pre, baseline_results, sim_dir)
                if relocated_pre is not None:
                    current_pre = relocated_pre
                continue

            if q == "/baseline":
                baseline_results = _run_or_show_baseline(pre, baseline_results, sim_dir)
                current_pre = pre
                continue

            if q == "/network":
                show_network(current_pre)
                continue

            if q == "/planning-analyst":
                _run_planning_analyst(api_key, session, sim_dir)
                continue

            if q == "/onboarding":
                _display_onboarding()
                continue

            if q == "/operational-costs":
                show_operational_costs(pre)
                continue

            if q.startswith("/language"):
                parts = user_query.strip().split()
                if len(parts) == 2:
                    lang = parts[1]
                    if lang in AVAILABLE_LANGS:
                        set_language(lang)
                        session.completer = WordCompleter(
                            _get_commands_for_lang(lang), pattern=r"/\w*", sentence=True
                        )
                        console.print(t("nl.language.set", label=AVAILABLE_LANGS[lang]))
                    else:
                        console.print(t("nl.language.unknown", code=lang, langs=", ".join(AVAILABLE_LANGS)))
                else:
                    label = AVAILABLE_LANGS.get(current_language(), current_language())
                    console.print(t("nl.language.current", label=label))
                    console.print(t("nl.language.available", langs=", ".join(f"{k} ({v})" for k, v in AVAILABLE_LANGS.items())))
                    console.print(t("nl.language.usage"))
                continue

            if q == "/clear":
                os.system("cls" if os.name == "nt" else "clear")
                continue

            if not user_query.strip():
                continue

            # ── What-if queries require baseline ──────────────────────────────
            if baseline_results is None:
                console.print(t("nl.baseline_required"))
                continue

            # ── Parse with Claude ─────────────────────────────────────────────
            console.print(t("nl.analysing"))

            try:
                parsed = _parse_with_claude(user_query, pre, api_key)
            except ImportError as exc:
                show_error(str(exc))
                return
            except Exception as exc:
                show_error(f"Could not parse query: {exc}")
                continue

            if not parsed or not parsed.get("changes"):
                console.print(t("nl.no_scenario"))
                continue

            # ── Explain and confirm ───────────────────────────────────────────
            console.print()
            console.print(Panel.fit(
                f"[bold]{t('nl.assistant_label')}:[/bold] {parsed['explanation']}",
                border_style="cyan",
            ))
            console.print()

            if not _ask_confirm(t("nl.confirm_run"), default=True):
                console.print(t("nl.scenario_skipped"))
                continue

            # ── Apply changes and re-solve ────────────────────────────────────
            changes = _to_changes(parsed["changes"])
            scenario_pre = apply_scenario(pre, changes)

            if parsed.get("run_relocate"):
                # Combined flow: skip intermediate results, go straight to relocation
                console.print()
                console.print(t("nl.running_relocation"))
                final_results, final_pre = _run_relocate(
                    scenario_pre, baseline_results, sim_dir,
                    skip_confirm=True, description=parsed["explanation"],
                )
                if final_results is not None:
                    if final_pre is not None:
                        current_pre = final_pre
                    insight = _interpret_results(
                        baseline_results, final_results,
                        parsed["explanation"], pre, final_pre, api_key,
                    )
                    if insight:
                        console.print()
                        console.print(Panel.fit(f"[dim]{insight}[/dim]", border_style="dim"))
            else:
                console.print()
                s41, s42, s43 = _run_all_solvers(scenario_pre)
                current_pre = scenario_pre
                show_comparison(baseline_results, (s41, s42, s43), parsed["explanation"])

                insight = _interpret_results(
                    baseline_results, (s41, s42, s43),
                    parsed["explanation"], pre, scenario_pre, api_key,
                )
                if insight:
                    console.print()
                    console.print(Panel.fit(f"[dim]{insight}[/dim]", border_style="dim"))

                scenario_label = _slugify(parsed["explanation"])
                try:
                    write_csv([s41, s42, s43], sim_dir, label=scenario_label, description=parsed["explanation"])
                except Exception as exc:
                    show_error(f"Failed to write CSV: {exc}")

            scenario_count += 1

        except KeyboardInterrupt:
            console.print(t("nl.interrupted"))
            break
        except Exception as exc:
            show_error(f"Unexpected error: {exc}")
            continue

    # ── Exit summary ──────────────────────────────────────────────────────────
    console.print()
    if scenario_count > 0:
        console.print(t("nl.complete", n=scenario_count))
    else:
        console.print(t("nl.no_scenarios"))
    console.print()


# ── Claude API integration ────────────────────────────────────────────────────

def _slugify(text: str, max_len: int = 40) -> str:
    """Convert free-form text to a safe filename stem."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:max_len].rstrip("_")


def _build_system_prompt(pre: PreprocessedData) -> str:
    cp_list = ", ".join(pre.cp_names)
    t_list = ", ".join(pre.terminal_names)
    t_caps = dict(zip(pre.terminal_names, pre.terminal_capacity_monthly.tolist()))
    terminal_letter_mappings = "\n".join(
        f'  \u2022 "Terminal {chr(65 + j)}" maps to "{pre.terminal_names[j]}".'
        for j in range(min(len(pre.terminal_names), 26))
    )
    lang = current_language()
    lang_instruction = (
        "Write the explanation field in Portuguese (Brazilian). "
        "Never use English technical terms — use only these Portuguese equivalents: "
        "payload → peso médio, "
        "overtime → hora extra, "
        "shift → turno, "
        "availability → disponibilidade, "
        "cycle time → tempo de ciclo, "
        "working days → dias úteis, "
        "fleet → frota, "
        "truck → caminhão, "
        "lane → corredor, "
        "terminal → terminal, "
        "collection point → ponto de coleta."
        if lang == "pt_BR"
        else "Write the explanation field in English."
    )

    return f"""You are a logistics fleet-sizing assistant.  The user wants to run \
what-if scenarios on a fleet model.  Respond ONLY with valid JSON — no extra text.  \
{lang_instruction}

CURRENT MODEL PARAMETERS
  Collection Points  : {cp_list}
  Unload Terminals   : {t_list}
  Shift hours        : {pre.working_hours_per_day:.2f} h
  Lunch break        : {pre.lunch_hours:.2f} h
  Overtime allowed   : {pre.overtime_hours:.2f} h
  Effective hours/day: {pre.effective_hours:.2f} h
  Working days/month : {pre.working_days}
  Truck payload      : {pre.payload:.1f} tons
  Loaded speed       : {pre.speed_loaded:.1f} km/h
  Empty speed        : {pre.speed_empty:.1f} km/h
  Truck availability : {pre.availability:.0%}
  Terminal capacities: {t_caps}
  Variable costs ($/km): {", ".join(f"{n}: {v:.4f}" for n, v in pre.cost_breakdown["variable"])}
  Fixed costs ($/truck/month): {", ".join(f"{n}: {v:,.2f}" for n, v in pre.cost_breakdown["fixed"])}
  Overtime rate      : ${pre.cost_overtime_driver:,.2f}/extra hour/truck

RESPONSE SCHEMA
{{
  "explanation": "<one or two sentences describing the scenario>",
  "changes": [
    {{
      "change_type"     : "<type>",
      "value"           : <absolute new value as float, or null>,
      "delta_abs"       : <amount to add/subtract as float, or null>,
      "delta_pct"       : <percent change as float (+15 = +15%), or null>,
      "targets"         : ["<CP node name>", ...] or null,
      "terminal_targets": ["<terminal node name>", ...] or null,
      "speed_loaded"    : <float or null>,
      "speed_empty"     : <float or null>,
      "component_name"  : "<exact component name for cost_variable or cost_fixed, or null>"
    }}
  ],
  "run_relocate": <true if the user also wants demand relocation optimised, false otherwise>
}}

CHANGE TYPES
  overtime           — Change overtime hours
  working_hours      — Change full shift hours
  working_days       — Change working days per month
  availability       — Change truck availability as a fraction (e.g. 0.90 for 90%); value is always 0–1
  payload            — Change truck payload in tons
  speed_loaded       — Change loaded truck speed (km/h)
  speed_empty        — Change empty truck speed (km/h)
  speed_both         — Change both speeds; use speed_loaded and speed_empty fields
  terminal_capacity  — Change terminal capacity (tons/month); targets = terminal names
  unload_time        — Change terminal unload time (h); targets = terminal names
  cp_capacity        — Change CP capacity (tons/month); targets = CP names
  demand             — Change monthly demand on a specific CP→Terminal lane or across CPs;
                       targets = CP names; terminal_targets = terminal names (for lane-specific changes)
  cost_variable      — Change a variable cost component ($/km); set component_name to the exact
                       component name (e.g. "Fuel", "Tires", "Tractor Maintenance",
                       "Trailer Maintenance", "Others")
  cost_fixed         — Change a fixed cost component ($/truck/month); set component_name to the
                       exact component name (e.g. "Depreciation", "Driver wage", "IPVA, Tax",
                       "Insurance", "Monitoring")
  cost_overtime      — Change the overtime rate ($/extra hour/truck); no component_name needed

RULES
  • For availability, always use a 0–1 fraction: "90%" → value: 0.90, "increase by 5%" → delta_pct: 5.
  • For time like "2:00" or "10:30" convert to decimal hours (2:00→2.0, 10:30→10.5).
  • "Increase to X" → value: X.  "Increase by X" → delta_abs: X.  "Increase by 15%" → delta_pct: 15.
  • "7 days per week" ≈ 28 working days/month.  "5 days per week" ≈ 20.  "6 days/week" ≈ 24.
{terminal_letter_mappings}
  • "CP 01" refers to the CP whose name contains "01" — use the exact name from the
    Collection Points list above (e.g. "Collection_Point01", not "Collection_Point_01").
  • Use only one of value / delta_abs / delta_pct per change object.
  • Targets must exactly match node names from the lists above.
  • For queries that modify more than one parameter, emit one change object per parameter —
    do NOT combine multiple changes into a single object.
  • For speed changes that affect both speeds differently, emit two separate change objects
    (change_type speed_loaded and speed_empty) OR one speed_both with both fields set.
  • If the user mentions "relocate", "relocation", "optimise distribution", or similar alongside
    a parameter change, set run_relocate: true.  Do NOT emit a separate change object for it.
  • For cost_variable and cost_fixed, set component_name exactly as listed in the CHANGE TYPES
    section above (e.g. "Driver wage", not "driver wages").  The match is case-insensitive but
    the exact words must be present.
  • "Set extra hours to $X" or "Set overtime rate to $X" → change_type: cost_overtime, value: X.
  • "Set fuel to $X/km" → change_type: cost_variable, component_name: "Fuel", value: X.
  • "Set driver wage to $X" → change_type: cost_fixed, component_name: "Driver wage", value: X.
  • For lane-specific demand changes (a specific CP→Terminal pair), set BOTH targets (CP name)
    AND terminal_targets (terminal name).  Example: "Set CP 01 to terminal A as 2943" →
    change_type: demand, value: 2943.0,
    targets: ["{pre.cp_names[0] if pre.cp_names else ''}"],
    terminal_targets: ["{pre.terminal_names[0] if pre.terminal_names else ''}"].
  • When only targets is set (no terminal_targets), the demand change applies to all terminals
    currently served by those CPs.
"""


def _parse_with_claude(query: str, pre: PreprocessedData, api_key: str) -> dict:
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "The 'anthropic' package is not installed.  Run: pip install anthropic"
        )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=_build_system_prompt(pre),
        messages=[{"role": "user", "content": query}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if the model wraps output
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            line for line in lines if not line.startswith("```")
        )

    # Use raw_decode so extra text or a second JSON object after the first is ignored
    obj, _ = json.JSONDecoder().raw_decode(raw)
    return obj


def _interpret_results(
    baseline: ScopeTriple,
    scenario: ScopeTriple,
    scenario_description: str,
    pre_baseline: "PreprocessedData",
    pre_scenario: "PreprocessedData",
    api_key: str,
) -> str:
    """Call Haiku to surface the non-obvious insight in the before/after results."""
    try:
        import anthropic
    except ImportError:
        return ""

    b41, b42, b43 = baseline
    s41, s42, s43 = scenario
    vol = b42.total_monthly_volume_tons

    def _fmt(r) -> str:
        cpt = r.monthly_operational_cost_usd / r.total_trucks if r.total_trucks else 0
        cts = r.monthly_operational_cost_usd / vol if vol else 0
        return (
            f"  trucks={r.total_trucks}, "
            f"op_cost=${r.monthly_operational_cost_usd:,.0f}/month, "
            f"cost_per_truck=${cpt:,.0f}, "
            f"cost_per_ton=${cts:.2f}"
        )

    def _var_cost(d) -> str:
        return ", ".join(f"{n}: ${v:.4f}/km" for n, v in d.cost_breakdown["variable"])

    def _fixed_cost(d) -> str:
        return ", ".join(f"{n}: ${v:,.2f}/truck/mo" for n, v in d.cost_breakdown["fixed"])

    # Only include parameters that actually changed — avoids sending noise to the model
    _params: list[str] = []

    def _p(name: str, b: str, s: str) -> None:
        if b != s:
            _params.append(f"  {name}: {b} → {s}")

    _p("availability",   f"{pre_baseline.availability:.0%}",             f"{pre_scenario.availability:.0%}")
    _p("effective_hours",f"{pre_baseline.effective_hours:.2f}h",         f"{pre_scenario.effective_hours:.2f}h")
    _p("overtime_hours", f"{pre_baseline.overtime_hours:.1f}h",          f"{pre_scenario.overtime_hours:.1f}h")
    _p("working_days",   str(pre_baseline.working_days),                 str(pre_scenario.working_days))
    _p("payload",        f"{pre_baseline.payload:.1f}t",                 f"{pre_scenario.payload:.1f}t")
    _p("speed_loaded",   f"{pre_baseline.speed_loaded:.1f} km/h",        f"{pre_scenario.speed_loaded:.1f} km/h")
    _p("speed_empty",    f"{pre_baseline.speed_empty:.1f} km/h",         f"{pre_scenario.speed_empty:.1f} km/h")
    _p("variable costs", _var_cost(pre_baseline),                        _var_cost(pre_scenario))
    _p("fixed costs",    _fixed_cost(pre_baseline),                      _fixed_cost(pre_scenario))
    _p("overtime_rate",  f"${pre_baseline.cost_overtime_driver:.2f}/h",  f"${pre_scenario.cost_overtime_driver:.2f}/h")

    param_block = (
        "\n".join(_params)
        if _params else
        "  (no model parameters changed — structural changes only)"
    )

    prompt = f"""Scenario applied: {scenario_description}

BASELINE
  4.1 Lane-by-Lane : {_fmt(b41)}
  4.2 Weighted Cycle: {_fmt(b42)}
  4.3 MILP          : {_fmt(b43)}

SCENARIO RESULT
  4.1 Lane-by-Lane : {_fmt(s41)}
  4.2 Weighted Cycle: {_fmt(s42)}
  4.3 MILP          : {_fmt(s43)}

MODEL PARAMETERS — changed values only (baseline → scenario)
{param_block}"""

    lang = current_language()
    lang_rule = (
        "- Write your response in Portuguese (Brazilian). "
        "Never use English technical terms — use only these Portuguese equivalents: "
        "payload → peso médio, overtime → hora extra, shift → turno, "
        "availability → disponibilidade, cycle time → tempo de ciclo, "
        "working days → dias úteis, fleet → frota, truck → caminhão, "
        "lane → corredor, collection point → ponto de coleta."
        if lang == "pt_BR"
        else "- Write your response in English."
    )

    system = f"""You are a logistics analyst reviewing fleet sizing results for an operations manager.

Your job: write ONE short paragraph (2–3 sentences max) that surfaces the non-obvious \
trade-off or implication in these results.

RULES — follow them strictly:
- Do NOT restate numbers the user can already see in the table (trucks saved, cost change %).
- Do NOT describe what the scenario is — the user set it themselves.
- DO explain WHY a result is counterintuitive, or what decision it forces.
- DO name the mechanism behind the numbers (e.g. which cost component is driving the change).
- Do NOT minimise cost increases with words like "only", "merely", or "just" — in logistics operations, even single-digit percentage increases represent significant budget impact.
- PROFITABILITY METRIC: the correct measure is cost per ton or total operational cost — NOT cost per truck. If total cost falls and fleet shrinks while serving the same demand, that is unambiguously better economics. Do NOT frame rising cost-per-truck as "profitability deterioration" when total cost is improving.
- CAUSAL DISCIPLINE: variable cost changes ($/km rates such as fuel, tires, maintenance) affect operational cost but do NOT drive fleet count. Fleet count is determined by cycle time, availability, shift hours, payload, and working days. Never attribute a fleet size change to a cost rate change.
- NO INVENTED NUMBERS: every dollar amount, percentage, or quantity you state must be directly readable from the BASELINE, SCENARIO RESULT, or MODEL PARAMETERS data above. Do not estimate, compute, or approximate any figure not explicitly provided.
- DIRECTION CHECK: before characterising any parameter as having increased or decreased, verify it against the MODEL PARAMETERS section above. baseline → scenario is the authoritative source of direction.
- COMBINED SCENARIOS: when the scenario contains changes with opposite effects — a headwind (any cost rate increase, lower availability, reduced payload, fewer working days, less overtime) and a tailwind (any structural improvement such as demand relocation, higher availability, increased payload, added overtime, more working days) — treat them as separate forces. Use the SCENARIO RESULT net outcome to determine which dominates, then frame accordingly: "the tailwind overcame the headwind", "the tailwind partially mitigated the headwind", or "the headwind overwhelmed the tailwind." Never frame a headwind as amplifying or contributing to savings, and never frame a tailwind as worsening costs.
- If the result is exactly what you would expect with no tension, say so in one sentence.
- No bullet points. No headers. Plain prose only.
{lang_rule}"""

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return ""


def _to_changes(raw: list[dict]) -> list[ScenarioChange]:
    return [
        ScenarioChange(
            change_type=c.get("change_type", ""),
            value=c.get("value"),
            delta_abs=c.get("delta_abs"),
            delta_pct=c.get("delta_pct"),
            targets=c.get("targets"),
            terminal_targets=c.get("terminal_targets"),
            speed_loaded=c.get("speed_loaded"),
            speed_empty=c.get("speed_empty"),
            component_name=c.get("component_name"),
        )
        for c in raw
    ]


# ── /planning-analyst handler ─────────────────────────────────────────────────

def _run_planning_analyst(api_key: str, session: PromptSession, sim_dir: Path) -> None:
    from .planning_analyst import run_planning_analyst, _load_scenario_summaries, append_memory
    data_block = _load_scenario_summaries(sim_dir)
    if not data_block:
        show_error("No scenario files found in outputs/. Run /baseline first.")
        return
    console.print()
    console.print(t("nl.analyst.sending"))
    console.print(f"[dim]{data_block}[/dim]")
    console.print(t("nl.analyst.divider"))
    console.print(t("nl.analyst.drafting"))
    try:
        report = run_planning_analyst(sim_dir, _OUTPUTS_BASE, api_key)
    except Exception as exc:
        show_error(f"Planning analyst failed: {exc}")
        return
    show_planning_analysis(report)

    # Step 1 — one-keypress rating
    console.print()
    console.print(t("nl.analyst.rating_prompt"))
    try:
        rating = session.prompt(t("nl.analyst.rating_input"), key_bindings=_RATING_BINDINGS).strip()
    except (KeyboardInterrupt, EOFError):
        rating = ""

    if rating not in ("2", "3"):
        return

    # Step 2 — optional free-text note (only for rating 2 or 3)
    console.print()
    console.print(t("nl.analyst.note_prompt"))
    try:
        user_note = session.prompt(t("nl.analyst.note_input"))
    except (KeyboardInterrupt, EOFError):
        user_note = ""

    try:
        append_memory(_OUTPUTS_BASE, report, user_note, rating)
    except Exception as exc:
        show_error(f"Could not save analyst memory: {exc}")


# ── /relocate handler ─────────────────────────────────────────────────────────

def _run_relocate(
    pre: PreprocessedData,
    baseline_results: ScopeTriple,
    sim_dir: Path,
    *,
    skip_confirm: bool = False,
    description: str | None = None,
) -> tuple[ScopeTriple | None, PreprocessedData | None]:
    """Optimise demand allocation across CPs and optionally apply as a scenario.

    When skip_confirm=True the inner confirmation prompt is suppressed (used when
    the user already confirmed a combined parameter + relocation query upfront).
    Returns (ScopeTriple, PreprocessedData) on success, (None, None) otherwise.
    """
    from . import relocate as _rel

    console.print()
    console.print(t("nl.relocate.optimising"))

    result = _rel.optimize_relocation(pre)

    if result.status != "Optimal":
        show_error(f"Relocation optimisation failed: {result.status}")
        return None, None

    show_relocation_result(result)

    if not result.changes:
        return None, None

    if not skip_confirm:
        if not _ask_confirm(t("nl.relocate.confirm"), default=True):
            console.print(t("nl.relocate.not_applied"))
            return None, None

    scenario_pre = apply_scenario(pre, result.changes)
    console.print()
    s41, s42, s43 = _run_all_solvers(scenario_pre)
    label = description or t("nl.relocate.default_label")
    show_comparison(baseline_results, (s41, s42, s43), label)
    csv_label = _slugify(description) if description else "optimal_demand_relocation"
    try:
        write_csv([s41, s42, s43], sim_dir, label=csv_label, description=label)
    except Exception as exc:
        show_error(f"Failed to write CSV: {exc}")
    return (s41, s42, s43), scenario_pre


# ── Display helpers ───────────────────────────────────────────────────────────

def _display_help() -> None:
    console.print()
    console.print(Panel.fit(
        t("nl.help.panel_title"),
        border_style="green",
    ))
    console.print()
    console.print(t("nl.help.commands_header"))
    console.print(t("nl.help.list"))
    console.print(t("nl.help.driver"))
    console.print(t("nl.help.whatif"))
    console.print(t("nl.help.relocate"))
    console.print(t("nl.help.baseline"))
    console.print(t("nl.help.onboarding"))
    console.print(t("nl.help.opcosts"))
    console.print(t("nl.help.analyst"))
    console.print(t("nl.help.network"))
    console.print(t("nl.help.language"))
    console.print(t("nl.help.help"))
    console.print(t("nl.help.clear"))
    console.print(t("nl.help.quit"))


def _display_onboarding() -> None:
    console.print()
    console.print(Panel.fit(
        t("nl.onboarding.panel_title"),
        border_style="cyan",
    ))
    console.print()
    console.print(t("nl.onboarding.lane_by_lane"))
    console.print()
    console.print(t("nl.onboarding.weighted_cycle"))
    console.print()
    console.print(t("nl.onboarding.milp"))
    console.print()


def _run_or_show_baseline(
    pre: PreprocessedData,
    existing: ScopeTriple | None,
    sim_dir: Path,
) -> ScopeTriple:
    """Run solvers if baseline not yet computed, then display results."""
    if existing is not None:
        show_baseline_detail(existing)
        return existing

    console.print()
    s41, s42, s43 = _run_all_solvers(pre)
    results: ScopeTriple = (s41, s42, s43)

    try:
        write_csv([s41, s42, s43], sim_dir, label="baseline", description="Baseline fleet sizing")
    except Exception as exc:
        show_error(f"Failed to write CSV: {exc}")

    show_baseline_detail(results)
    return results
