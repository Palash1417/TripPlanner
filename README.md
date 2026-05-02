# Travel Planning Multi-Agent System

A multi-agent system that turns a free-form travel request like
*"Plan a 5-day trip to Japan. Tokyo + Kyoto. $3,000 budget. Love food and temples, hate crowds."*
into a validated, budget-aware day-by-day plan.

> **Status:** Phase 8 (observability, cost & quality tracking) complete. See [doc/implementation.md](doc/implementation.md) for the build plan.

## Documentation

- [doc/problemStatement.md](doc/problemStatement.md) — what we're building and why
- [doc/architecture.md](doc/architecture.md) — 9-phase system architecture
- [doc/implementation.md](doc/implementation.md) — phase-wise build plan
- [doc/edgeCase.md](doc/edgeCase.md) — edge case catalog (P0 / P1 / P2)

## Setup

```bash
# 1. Create a virtualenv
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure your API key
cp .env.example .env
# then edit .env and set GEMINI_API_KEY=...
# Get a free Gemini key at https://aistudio.google.com/app/apikey
```

> **LLM provider:** This project uses **Google Gemini** (free tier — 15 RPM, 1M tokens/day on Flash). The `LLMClient` interface is provider-agnostic, so swapping back to Claude or another model is a single-file change.

## Running Phase 0 (raw smoke test)

```bash
python -m src.main "hello, can you plan a quick weekend trip?"
```

Returns a one-sentence Gemini Flash reply + a trace file under `traces/`.

## Running the CLI (full pipeline + clarification)

```bash
# Interactive — pauses to ask if Intent surfaces open_questions
python -m src.ui.cli "Plan a 5-day trip to Japan. Tokyo + Kyoto. \$3,000 budget."

# Non-interactive — proceeds with assumptions, useful for scripting
python -m src.ui.cli --no-clarify "Plan a 5-day trip to Japan."
```

Renders the full plan: TripBrief → POIs → Stays → Transport → Budget → Itinerary → Critic verdict.

## Running the Streamlit UI

```bash
streamlit run src/ui/streamlit_app.py
```

Opens a browser at http://localhost:8501. Type a free-form trip request, hit "Plan my trip", and watch each agent run in real time via `st.status` containers. Final plan renders as expandable sections with metrics, dataframes, and a critic verdict.

## Running tests

```bash
pytest -q
```

The default test run does **not** hit the live API. To run live-LLM tests (added from Phase 2 onward):

```bash
RUN_LIVE=1 pytest -q -m live
```

## Quality report (Phase 8)

Every planning run emits a single `trip_summary` event at the end of its trace
file with totals (tokens, cost, wall time), the critic verdict, revision count,
and any rule violations. To aggregate those across a directory of traces:

```bash
python scripts/quality_report.py            # ./traces, human-readable
python scripts/quality_report.py --json     # machine-readable for piping
python scripts/quality_report.py path/to/traces
```

Reports critic pass-rate, mean / p50 / max cost and wall time, top failing
rules, and the agents most often blamed for failures. Run it after a sweep of
live trips to answer *"is the system getting better or worse this week?"*.

## Repository layout

```
TripPlannerMultiAgentSystem/
├── doc/                    # all design docs
├── src/
│   ├── agents/             # one file per specialist agent (Phase 2–5)
│   ├── orchestrator/       # execution graph + shared state (Phase 2+)
│   ├── schemas/            # Pydantic data contracts (Phase 1)
│   ├── tools/              # external integrations (Phase 6)
│   ├── llm/                # Claude client wrapper (Phase 0)
│   ├── observability/      # per-trip tracer (Phase 0)
│   ├── ui/                 # CLI + Streamlit (Phase 7)
│   └── main.py             # Phase 0 entry point
├── tests/                  # unit + integration + fixtures
├── traces/                 # JSONL trace dumps (gitignored)
├── scripts/                # ops scripts (quality_report etc.)
├── pyproject.toml
├── requirements.txt
└── .env.example
```

The folder structure follows [doc/implementation.md](doc/implementation.md) — every placeholder file's docstring names the phase that implements it.

## Adding a new agent (forward-looking)

1. Add a Pydantic schema in `src/schemas/`.
2. Add the agent module in `src/agents/`. Import `LLMClient`, define a system prompt as a module-level constant, and a top-level function `run(brief, ...)` returning the schema.
3. Wire it into `src/orchestrator/graph.py`.
4. Add a unit test (mocked LLM) in `tests/unit/` and an integration test in `tests/integration/`.

## Known limitations (v1)

- No live booking, no real-time pricing, no visa/insurance handling.
- Estimates are clearly labeled as estimates.
- See [doc/edgeCase.md](doc/edgeCase.md) for the full P0/P1/P2 list.
