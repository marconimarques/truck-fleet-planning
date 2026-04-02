"""Planning analyst subagent — synthesises all scenario CSVs into a strategic report."""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path


def _label_from_stem(stem: str) -> str:
    """Derive a readable scenario label from a CSV filename stem."""
    if "_output_v" in stem:
        name_part, ver = stem.rsplit("_output_v", 1)
        return f"{name_part.replace('_', ' ')} [v{ver}]"
    return stem


def _fmt_trucks(row: dict, field: str = "trucks_needed") -> str:
    val = row.get(field, "").strip()
    return val if val else "n/a"


def _fmt_cost(row: dict, field: str = "monthly_operational_cost_usd") -> str:
    val = row.get(field, "").strip()
    try:
        return f"${float(val):,.0f}/month" if val else "n/a"
    except ValueError:
        return val or "n/a"


def _fmt_trips(row: dict, field: str = "trips_month") -> str:
    val = row.get(field, "").strip()
    try:
        return f"{int(float(val)):,} trips/month" if val else "n/a"
    except ValueError:
        return val or "n/a"


def _fmt_rate(row: dict, field: str = "trips_per_truck_per_day") -> str:
    val = row.get(field, "").strip()
    try:
        return f"{float(val):.2f} trips/truck/day" if val else "n/a"
    except ValueError:
        return val or "n/a"


def _load_scenario_summaries(outputs_dir: Path) -> str:
    """Read all *_output_v*.csv files and return an explicit block for the analyst."""
    if not outputs_dir.exists():
        return ""
    all_files = sorted(outputs_dir.glob("*_output_v*.csv"))
    csv_files = sorted(all_files, key=lambda f: not f.stem.startswith("baseline"))
    if not csv_files:
        return ""

    blocks: list[str] = []
    for idx, csv_path in enumerate(csv_files, start=1):
        label = _label_from_stem(csv_path.stem)

        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                all_rows = list(csv.DictReader(f))
        except Exception as exc:
            blocks.append(f"SCENARIO {idx}: {label}\n  [error reading file: {exc}]")
            continue

        # Summary rows only (cp_name == "ALL")
        summary = {r["scope"].strip(): r for r in all_rows if r.get("cp_name", "").strip() == "ALL"}

        r41 = summary.get("4.1_lane_by_lane", {})
        r42 = summary.get("4.2_weighted_cycle", {})
        r43 = summary.get("4.3_milp", {})

        # Prefer the stored full description over the filename-derived label
        for row in (r41, r42, r43):
            stored = row.get("scenario_description", "").strip()
            if stored:
                label = stored
                break

        block = (
            f"SCENARIO {idx}: {label}\n"
            f"  4.1 Lane-by-Lane  (conservative upper bound) : {_fmt_trucks(r41)} trucks   {_fmt_cost(r41)}\n"
            f"  4.2 Weighted Cycle (operational target)       : {_fmt_trucks(r42)} trucks   {_fmt_cost(r42)}\n"
            f"  4.3 MILP Optimal   (most accurate)            : {_fmt_trucks(r43)} trucks   {_fmt_cost(r43)}"
        )
        blocks.append(block)

    return "\n\n".join(blocks)


def _load_memory(memory_dir: Path) -> str:
    """Return the contents of analyst_memory.md, or empty string if none exists."""
    path = memory_dir / "analyst_memory.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _audit_report(draft: str, data_summary: str, api_key: str) -> str:
    """Second Haiku call: fact-check the draft report against the raw numbers."""
    import anthropic
    from .i18n import current_language

    auditor_prompt = (Path(__file__).parent / "planning_analyst_auditor.md").read_text(encoding="utf-8")
    if current_language() == "pt_BR":
        auditor_prompt += "\n\nRespond entirely in Brazilian Portuguese (pt-BR)."
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=auditor_prompt,
        messages=[{
            "role": "user",
            "content": f"RAW DATA:\n{data_summary}\n\nDRAFT REPORT:\n{draft}",
        }],
    )
    return response.content[0].text


def append_memory(memory_dir: Path, report: str, user_note: str = "", rating: str = "") -> None:
    """Distil the analyst report into a dated memory entry and append it to analyst_memory.md."""
    rec_match = re.search(
        r"^[^\n]*STRATEGIC RECOMMENDATION[^\n]*\n(.+)",
        report,
        re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    recommendation = rec_match.group(1).strip() if rec_match else "(not extracted)"

    risk_match = re.search(
        r"RISK FLAGS\s*\n(.+?)(?=\n[A-Z ]{4,}|\Z)", report, re.DOTALL
    )
    risk_flags = risk_match.group(1).strip() if risk_match else ""

    entry_parts = [f"\n## [{date.today().isoformat()}]"]
    if rating:
        entry_parts.append(f"### Rating: {rating}")
    entry_parts.append(f"### Recommendation\n{recommendation}")
    if risk_flags:
        entry_parts.append(f"### Risk flags\n{risk_flags}")
    if user_note.strip():
        entry_parts.append(f"### User note\n{user_note.strip()}")

    entry = "\n".join(entry_parts) + "\n"

    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / "analyst_memory.md"
    if not path.exists():
        path.write_text("# Analyst Memory\n" + entry, encoding="utf-8")
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)


def run_planning_analyst(outputs_dir: Path, memory_dir: Path, api_key: str) -> str:
    """Call the Claude planning analyst subagent and return its audited strategic report."""
    import anthropic
    from .i18n import current_language

    system_prompt = (Path(__file__).parent / "planning_analyst.md").read_text(encoding="utf-8")
    if current_language() == "pt_BR":
        system_prompt += "\n\nRespond entirely in Brazilian Portuguese (pt-BR)."

    data_summary = _load_scenario_summaries(outputs_dir)
    if not data_summary:
        return "No scenario files found in outputs/. Run /baseline and at least one what-if scenario first."

    memory = _load_memory(memory_dir)

    user_parts = ["Fleet sizing results across all scenarios:\n\n", data_summary, "\n\n"]
    if memory:
        user_parts.append(f"MEMORY — your previous analyses on this fleet:\n\n{memory}\n\n")
    user_parts.append(
        "The first scenario labelled 'baseline' is the reference point. "
        "All others are what-if scenarios. Provide your strategic planning analysis."
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": "".join(user_parts)}],
    )
    draft = response.content[0].text

    return _audit_report(draft, data_summary, api_key)
