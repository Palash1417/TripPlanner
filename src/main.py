"""Phase 0 entry point: free-form text → traced Claude round-trip.

Usage:
    python -m src.main "hello"
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv
from rich.console import Console

from src.llm import LLMClient
from src.observability import Tracer

console = Console()


def run(user_text: str) -> int:
    load_dotenv()
    tracer = Tracer()
    client = LLMClient()

    console.print(f"[bold cyan]trip_id:[/] {tracer.trip_id}")
    console.print(f"[bold cyan]input:[/]   {user_text}")

    try:
        with tracer.span("phase0_smoke", input_payload={"text": user_text}) as span:
            response = client.call(
                system="You are a concise travel-planning assistant under construction. Reply in 1 sentence.",
                user=user_text,
                tier="fast",
                max_tokens=200,
            )
            span.record_llm(response)
            span.set_output({"reply": response.text})
    except Exception as e:
        console.print(f"[bold red]error:[/] {e}")
        return 1

    console.print(f"[bold green]reply:[/]   {response.text}")
    console.print(f"[dim]model={response.model} latency={response.latency_ms}ms "
                  f"tokens={response.input_tokens}+{response.output_tokens} "
                  f"cost=${response.cost_usd:.6f}[/]")
    console.print(f"[dim]trace:   {tracer.path}[/]")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        console.print("[yellow]usage:[/] python -m src.main \"<your message>\"")
        return 2
    return run(" ".join(argv))


if __name__ == "__main__":
    raise SystemExit(main())
