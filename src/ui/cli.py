"""Phase 2-7 CLI: free text → TripBrief → Itinerary, all pretty-printed.

Usage:
    python -m src.ui.cli "Plan a 5-day trip to Japan. Tokyo + Kyoto. $3,000 budget."
    python -m src.ui.cli --no-clarify "..."   # skip interactive clarification

Phase 7: if Intent surfaces open_questions, the CLI pauses and prompts the user
for refinements via stdin (interactive). Pass --no-clarify to disable.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

# Windows default cp1252 can't print our Unicode arrows / emojis. Reconfigure
# stdout/stderr to UTF-8 (Python 3.7+). Harmless on macOS/Linux.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 — best effort; never fail at import
            pass

from src.orchestrator import TripContext, complete_plan, emit_trip_summary, run_intent
from src.schemas import (
    AccommodationPlan,
    BudgetBreakdown,
    CriticVerdict,
    DestinationCatalog,
    Itinerary,
    TransportPlan,
    TripBrief,
)

console = Console()


def render_brief(brief: TripBrief) -> None:
    tree = Tree("[bold]TripBrief[/]")

    tree.add(f"[cyan]origin:[/] {_or_dash(brief.origin)}")
    tree.add(f"[cyan]destinations:[/] {', '.join(brief.destinations)}")
    tree.add(f"[cyan]duration_days:[/] {_or_dash(brief.duration_days)}")

    if brief.budget:
        tree.add(f"[cyan]budget:[/] {brief.budget.amount:.0f} {brief.budget.currency}")
    else:
        tree.add("[cyan]budget:[/] —")

    tree.add(
        f"[cyan]travelers:[/] {brief.travelers.adults} adult(s), "
        f"{brief.travelers.children} child(ren)"
    )

    prefs = tree.add("[cyan]preferences[/]")
    prefs.add(f"likes: {_join(brief.preferences.likes)}")
    prefs.add(f"dislikes: {_join(brief.preferences.dislikes)}")
    prefs.add(f"pace: {_or_dash(brief.preferences.pace)}")
    prefs.add(f"accessibility: {_join(brief.preferences.accessibility)}")

    dates_label = (
        f"{brief.dates.start} → {brief.dates.end}"
        if brief.dates.start and brief.dates.end
        else "—"
    )
    tree.add(f"[cyan]dates:[/] {dates_label} (flexible={brief.dates.flexible})")

    if brief.open_questions:
        oq = tree.add("[yellow]open_questions[/]")
        for q in brief.open_questions:
            oq.add(f"[yellow]?[/] {q}")
    else:
        tree.add("[green]open_questions:[/] (none)")

    console.print(tree)


def render_destinations(catalog: DestinationCatalog) -> None:
    grouped = catalog.by_city()
    table = Table(title="Destination POIs", border_style="blue")
    table.add_column("City", style="cyan")
    table.add_column("POI")
    table.add_column("Category", style="dim")
    table.add_column("Visit", justify="right")
    table.add_column("$", justify="right")
    table.add_column("Why")
    for city, pois in grouped.items():
        for p in pois:
            table.add_row(
                city,
                p.name,
                p.category,
                f"{p.est_visit_minutes}m",
                f"${p.est_cost_usd:.0f}" if p.est_cost_usd else "free",
                "; ".join(p.match_reasons[:2]),
            )
    console.print(table)


def render_accommodation(plan: AccommodationPlan) -> None:
    table = Table(title=f"Stays — total ~${plan.total_usd:.0f}", border_style="magenta")
    table.add_column("City", style="cyan")
    table.add_column("Neighborhood")
    table.add_column("Property")
    table.add_column("Type", style="dim")
    table.add_column("Nights", justify="right")
    table.add_column("$/night", justify="right")
    table.add_column("Total", justify="right")
    for s in plan.stays:
        table.add_row(
            s.city,
            s.neighborhood,
            s.name,
            s.property_type,
            str(s.nights),
            f"${s.price_per_night_usd:.0f}",
            f"${s.total_usd:.0f}",
        )
    console.print(table)


def render_transport(plan: TransportPlan) -> None:
    table = Table(title=f"Transport — total ~${plan.total_usd:.0f}", border_style="green")
    table.add_column("From", style="cyan")
    table.add_column("To", style="cyan")
    table.add_column("Mode")
    table.add_column("Duration", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Notes", style="dim")
    for leg in plan.legs:
        hh, mm = divmod(leg.duration_minutes, 60)
        table.add_row(
            leg.origin,
            leg.destination,
            leg.mode,
            f"{hh}h{mm:02d}m",
            f"${leg.cost_usd:.0f}",
            leg.notes or "",
        )
    console.print(table)


def render_budget(budget_obj: BudgetBreakdown) -> None:
    title = f"Budget — ${budget_obj.total_estimate_usd:.0f} {budget_obj.currency}"
    if budget_obj.budget_amount_usd is not None:
        title += f" / ${budget_obj.budget_amount_usd:.0f} cap"
    border = "yellow" if budget_obj.warnings else "green"
    table = Table(title=title, border_style=border)
    table.add_column("Category", style="cyan")
    table.add_column("Estimate", justify="right")
    table.add_column("Confidence", style="dim")
    table.add_column("Notes")
    for line in budget_obj.lines:
        table.add_row(
            line.category,
            f"${line.estimate_usd:.0f}",
            line.confidence,
            line.notes or "",
        )
    console.print(table)
    for w in budget_obj.warnings:
        console.print(f"[yellow]⚠[/] {w}")


def render_critic(verdicts: list[CriticVerdict]) -> None:
    if not verdicts:
        return
    final = verdicts[-1]
    title = (
        f"Critic — passed (rev {final.revision})"
        if final.passed
        else f"Critic — FAILED (rev {final.revision}, {len(final.violations)} violation(s))"
    )
    border = "green" if final.passed else "red"
    if not final.violations and final.passed:
        console.print(Panel(f"[green]✓ all rules passed (revision {final.revision})[/]",
                            title="Critic", border_style="green"))
        return
    table = Table(title=title, border_style=border)
    table.add_column("Rule", style="cyan")
    table.add_column("Severity")
    table.add_column("Agent", style="dim")
    table.add_column("Message")
    for v in final.violations:
        table.add_row(
            v.rule,
            f"[red]{v.severity}[/]" if v.severity == "fail" else f"[yellow]{v.severity}[/]",
            v.agent,
            v.message,
        )
    console.print(table)
    if len(verdicts) > 1:
        console.print(
            f"[dim]critic ran {len(verdicts)} time(s) "
            f"({sum(1 for v in verdicts if v.passed)} passing)[/]"
        )


def render_itinerary(itin: Itinerary) -> None:
    md_lines = [f"# Itinerary _(confidence: {itin.confidence})_"]
    if itin.summary:
        md_lines.append(f"\n> {itin.summary}\n")

    for day in itin.days:
        md_lines.append(f"\n## Day {day.day_number} — {day.city}")
        md_lines.append(f"_total scheduled: {day.total_activity_minutes} min_\n")

        if day.transport_leg:
            t = day.transport_leg
            hh, mm = divmod(t.duration_minutes, 60)
            md_lines.append(
                f"**🚄 Transit:** {t.origin} → {t.destination} "
                f"via {t.mode} ({hh}h{mm:02d}m, ~${t.cost_usd:.0f})"
            )
            if t.notes:
                md_lines.append(f"  _{t.notes}_")

        for act in day.activities:
            time_str = act.start_time.strftime("%H:%M") if act.start_time else "—"
            cost_str = f" · ~${act.est_cost_usd:.0f}" if act.est_cost_usd else ""
            md_lines.append(
                f"- **{time_str}** · {act.title} _({act.duration_minutes}m{cost_str})_"
            )
            if act.match_reasons:
                md_lines.append(f"    - _{', '.join(act.match_reasons)}_")
            if act.notes:
                md_lines.append(f"    - {act.notes}")

        if day.notes:
            md_lines.append(f"\n> {day.notes}")

    console.print(Markdown("\n".join(md_lines)))


def _render_run_summary(context: TripContext) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    totals = context.tracer.totals
    table.add_row("trip_id", context.trip_id)
    table.add_row("trace", str(context.tracer.path))
    table.add_row(
        "tokens",
        f"{totals['input_tokens']} in / {totals['output_tokens']} out",
    )
    table.add_row("cost", f"${totals['cost_usd']:.6f}")
    table.add_row("decisions", " · ".join(context.decisions) if context.decisions else "(none)")
    console.print(Panel(table, title="run summary", border_style="dim"))


def _or_dash(value) -> str:
    return str(value) if value is not None and value != "" else "—"


def _join(values: list[str]) -> str:
    return ", ".join(values) if values else "(none)"


def _clarify(brief, *, prompter=input) -> str:
    """If brief has open_questions, prompt user for refinements via stdin.
    Returns appended-context string, or "" if user wants to proceed with assumptions.
    """
    if not brief.open_questions:
        return ""

    console.print("\n[yellow]The agent has questions before continuing:[/]")
    for i, q in enumerate(brief.open_questions, 1):
        console.print(f"  [yellow]{i}.[/] {q}")
    console.print(
        "\n[dim]Type one answer per line. Blank line proceeds with assumptions.[/]"
    )

    answers: list[str] = []
    while True:
        try:
            line = prompter("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        answers.append(line)
    return "\n".join(answers)


def run(user_request: str, *, no_clarify: bool = False) -> int:
    load_dotenv()
    context = TripContext(user_request=user_request)

    console.print(Panel(user_request, title="user request", border_style="cyan"))

    # --- Intent phase ---
    try:
        run_intent(context)
    except Exception as e:
        console.print(f"[bold red]error during intent:[/] {e}")
        emit_trip_summary(context)
        _render_run_summary(context)
        return 1

    if context.brief:
        render_brief(context.brief)

    # --- Clarification (Phase 7) ---
    if context.brief and not no_clarify:
        addendum = _clarify(context.brief)
        if addendum:
            console.print("[cyan]applying clarifications and re-parsing...[/]")
            context.user_request = f"{user_request}\n\nAdditional context: {addendum}"
            try:
                run_intent(context)
            except Exception as e:
                console.print(f"[bold red]error during re-parse:[/] {e}")
                emit_trip_summary(context)
                _render_run_summary(context)
                return 1
            render_brief(context.brief)

    # --- Specialists / Budget / Itinerary / Critic ---
    try:
        complete_plan(context)
    except Exception as e:
        console.print(f"[bold red]error during planning:[/] {e}")
        _render_partials(context)
        _render_run_summary(context)
        return 1

    _render_partials(context)
    if context.itinerary:
        render_itinerary(context.itinerary)
    if context.critic_verdicts:
        render_critic(context.critic_verdicts)
    _render_run_summary(context)
    return 0


def _render_partials(context: TripContext) -> None:
    """Render whichever specialist outputs are present (used on success + error)."""
    if context.destination_catalog:
        render_destinations(context.destination_catalog)
    if context.accommodation_plan:
        render_accommodation(context.accommodation_plan)
    if context.transport_plan:
        render_transport(context.transport_plan)
    if context.budget:
        render_budget(context.budget)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    no_clarify = False
    if "--no-clarify" in argv:
        argv = [a for a in argv if a != "--no-clarify"]
        no_clarify = True
    if not argv:
        console.print(
            '[yellow]usage:[/] python -m src.ui.cli [--no-clarify] "<travel request>"'
        )
        return 2
    return run(" ".join(argv), no_clarify=no_clarify)


if __name__ == "__main__":
    raise SystemExit(main())
