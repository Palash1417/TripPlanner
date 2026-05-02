# Travel Planning Multi-Agent System — Phase-Wise Implementation Plan

> References: [problemStatement.md](problemStatement.md) · [architecture.md](architecture.md)

This document turns the architecture into an executable build plan. Each phase has: **scope**, **tasks**, **deliverables**, **acceptance tests**, and **exit criteria**. Phases ship a working artifact — no phase is "internal scaffolding only."

---

## Target Repository Layout (end state)

```
TripPlannerMultiAgentSystem/
├── doc/
│   ├── problemStatement.md
│   ├── architecture.md
│   └── implementation.md          ← this file
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── intent.py
│   │   ├── destination.py
│   │   ├── accommodation.py
│   │   ├── transport.py
│   │   ├── budget.py
│   │   ├── itinerary.py
│   │   └── critic.py
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── graph.py               ← execution graph / state machine
│   │   └── trip_context.py        ← shared blackboard
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── trip_brief.py
│   │   ├── poi.py
│   │   ├── stay.py
│   │   ├── transport.py
│   │   ├── budget.py
│   │   └── itinerary.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── web_search.py
│   │   ├── place_details.py
│   │   ├── geo.py
│   │   ├── currency.py
│   │   └── transport_lookup.py
│   ├── llm/
│   │   ├── __init__.py
│   │   └── client.py              ← LLM wrapper (Gemini), model selection
│   ├── observability/
│   │   ├── __init__.py
│   │   └── tracer.py
│   ├── ui/
│   │   ├── cli.py
│   │   └── streamlit_app.py
│   └── main.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│       └── sample_requests.json
├── traces/                        ← runtime trace dumps, gitignored
├── .env.example
├── requirements.txt
├── pyproject.toml
└── README.md
```

This layout maps 1:1 to the agents, schemas, and tools defined in [architecture.md](architecture.md).

---

## Phase 0 — Project Bootstrap (½ day)

**Scope:** Get a runnable, tested, traceable empty shell.

### Tasks
1. `pyproject.toml` with: `pydantic`, `anthropic`, `pytest`, `python-dotenv`, `rich` (CLI), `httpx` (tools).
2. `.env.example` with `ANTHROPIC_API_KEY=`. README explains setup.
3. `src/llm/client.py` — single Gemini client wrapper with model selection (`fast` → Flash / `smart` → Pro) and a `call(system, user, schema=None)` helper that returns either text or a Pydantic-parsed object.
4. `src/observability/tracer.py` — context-manager-based tracer that logs `{trip_id, agent, input, output, latency_ms, tokens}` to `traces/<trip_id>.jsonl`.
5. `tests/` skeleton with one smoke test that imports every module.
6. CI-equivalent local task: `pytest -q` must pass.

### Deliverables
- Repo runs `python -m src.main "hello"` → returns a Gemini reply, dumps a trace file.

### Acceptance
- `pytest -q` green.
- `traces/<trip_id>.jsonl` exists and is valid JSONL after a run.

### Exit criteria
- One developer can clone, set the API key, and get a traced LLM round-trip in under 5 minutes.

---

## Phase 1 — Schemas & Contracts (½ day)

**Scope:** Lock the data contracts from [architecture.md §3](architecture.md) before any agent is written.

### Tasks
1. `schemas/trip_brief.py` — Pydantic model matching `TripBrief` JSON in §3.1.
2. `schemas/poi.py`, `stay.py`, `transport.py`, `budget.py`, `itinerary.py` — one model per artifact, every recommendation carries `match_reasons: list[str]`.
3. `schemas/__init__.py` — re-export all models.
4. Unit tests round-trip each model: `Model(**fixture).model_dump() == fixture`.
5. Add a `confidence: Literal["high","medium","low"]` field to every estimate-bearing model.

### Deliverables
- All schemas importable, validated, fixture-tested.

### Acceptance
- `tests/unit/test_schemas.py` covers happy-path + one invalid-input rejection per model.

### Exit criteria
- No agent code references raw dicts. Every inter-agent payload is a Pydantic model.

---

## Phase 2 — Intent Agent + CLI Skeleton (1 day)

**Scope:** First end-to-end vertical slice: free text → `TripBrief` → printed to terminal.

### Tasks
1. `agents/intent.py` — system prompt that extracts a `TripBrief` from free-form text, returns Pydantic-validated output. Uses Gemini Flash (per architecture §8.1).
2. Prompt must populate `open_questions` when fields are missing rather than hallucinating values.
3. `orchestrator/trip_context.py` — `TripContext` class holding `trip_id`, `brief`, partial outputs, decision log.
4. `ui/cli.py` — accepts a request string, instantiates `TripContext`, runs Intent, pretty-prints the brief.
5. `tests/fixtures/sample_requests.json` — 5 sample requests including the Japan example, an underspecified one ("weekend somewhere warm"), and an overconstrained one.
6. `tests/integration/test_intent.py` — runs Intent on each fixture, asserts schema validity (not exact content — that's brittle).

### Deliverables
- `python -m src.ui.cli "Plan a 5-day trip to Japan..."` prints a parsed `TripBrief`.

### Acceptance
- Intent extracts Japan example correctly: `destinations=[Tokyo, Kyoto]`, `duration_days=5`, `budget=3000 USD`, `likes` includes `food` and `temples`.
- For underspecified input, `open_questions` is non-empty.

### Exit criteria
- Trace file shows one Intent call per CLI invocation. No silent failures.

---

## Phase 3 — Itinerary Builder + Skeleton Plan (1 day)

**Scope:** Architecture milestone **M1 — Skeleton**. Intent → Itinerary, LLM-only, no specialists, no Critic.

### Tasks
1. `agents/itinerary.py` — takes a `TripBrief`, produces an `Itinerary` (list of `ItineraryDay`) using LLM-only knowledge. No tool calls yet.
2. `orchestrator/graph.py` — minimal state machine: `intent → itinerary → render`.
3. `ui/cli.py` extended to render the itinerary as Markdown.
4. Integration test: Japan request → itinerary with 5 days, each day has ≥ 2 activities, final-day pacing is sensible.

### Deliverables
- End-to-end run: free text in, Markdown trip plan out.

### Acceptance
- Plan has correct day count.
- Plan mentions both Tokyo and Kyoto.
- Total LLM cost per run logged in trace.

### Exit criteria — M1 ships
- A non-technical user could read the output and understand the trip. Quality is not the goal yet — coverage is.

---

## Phase 4 — Specialist Agents (2–3 days)

**Scope:** Architecture milestone **M2 — Specialists**. Add Destination, Accommodation, Transport, Budget. Still LLM-only (no external tools).

### Tasks (one sub-phase per agent, can be parallelized after schemas exist)

#### 4a. Destination Research Agent
- `agents/destination.py` — input: `TripBrief`. Output: `dict[city, list[POI]]`.
- Prompt enforces every POI carries `match_reasons` referencing the brief.
- Test: every returned POI has at least one `match_reasons` entry tied to a `like` or `dislike`.

#### 4b. Accommodation Agent
- `agents/accommodation.py` — input: `TripBrief` + POI list. Output: `list[StayOption]` with neighborhood + price band per city.
- Test: each city in `destinations` has ≥ 1 stay option.

#### 4c. Transport Agent
- `agents/transport.py` — input: cities + duration. Output: `list[TransportLeg]` covering inter-city movement.
- Test: legs form a connected path through `destinations` in some order.

#### 4d. Budget Agent
- `agents/budget.py` — input: all cost estimates. Output: `BudgetBreakdown` with categories + a `warnings` list when projected total > brief budget.
- Test: warnings list is empty for a generous budget, populated for a tight one.

#### 4e. Orchestrator update
- `graph.py` — fan-out Destination ‖ Accommodation ‖ Transport in parallel after Intent (use `asyncio.gather` or `concurrent.futures`).
- Then Budget → Itinerary Builder (now consumes specialist outputs instead of raw brief).

### Deliverables
- Plan now references real-feeling neighborhoods, transport options, and a concrete budget table.

### Acceptance
- Japan example: plan mentions Shinkansen, names at least one Tokyo neighborhood and one Kyoto neighborhood, budget table sums to ≤ $3000.
- Parallel agents visible in trace as overlapping spans.

### Exit criteria — M2 ships
- Plan quality jumps noticeably vs. M1. Trace shows the fan-out.

---

## Phase 5 — Critic Agent + Revision Loop (1–2 days)

**Scope:** Architecture milestone **M3**. Add validation and revision.

### Tasks
1. `agents/critic.py` — input: `TripBrief` + draft plan. Output: `CriticVerdict { passed: bool, violations: list[Violation] }` where each violation cites the rule (§7.1) and the offending agent.
2. Implement the 5 rules from architecture §7.1 as **deterministic Python checks first**, then have the LLM check anything not mechanically expressible (e.g., "feels rushed").
3. `orchestrator/graph.py` — after Itinerary, run Critic. On failure, route to the guilty agent for one re-run. Hard cap at 2 revision rounds.
4. Trace each revision round with `revision: 0|1|2` field.
5. Tests:
   - Hand-crafted broken plan (over budget) → critic catches it, orchestrator re-runs Budget+Itinerary, second pass succeeds.
   - Plan missing a `like` coverage → critic catches it.
   - 2-round cap respected → returns best-effort with `warnings`.

### Deliverables
- A measurable quality gate. We can now report `% of plans that pass critic on first try`.

### Acceptance
- All 5 §7.1 rules have a unit test triggering them.
- Japan example passes critic on first or second round in ≥ 80% of runs (sample size 10).

### Exit criteria — M3 ships
- We have a number we can drive: critic-pass rate.

---

## Phase 6 — Tools & External Data (2–3 days)

**Scope:** Architecture milestone **M4**. Replace LLM-only knowledge with real lookups where it matters most.

### Tasks (priority order — ship each as it lands, do not block on the full set)

#### 6a. `tools/geo.py` (no API key needed)
- Haversine distance between two `(lat, lon)` points.
- Used by Itinerary Builder for the geographic-feasibility check.

#### 6b. `tools/currency.py`
- Static rates table loaded from JSON. `convert(amount, from_ccy, to_ccy)`.
- Wire into Budget Agent so it normalizes everything to the brief's currency.

#### 6c. `tools/web_search.py`
- Thin wrapper around a search API (Brave / Serper / Tavily — pick one, document choice in README).
- Returns `list[{title, url, snippet}]` — never raw HTML (architecture §5.2).
- Wire into Destination + Accommodation Agents.

#### 6d. `tools/place_details.py` (optional, gated by API key)
- Maps API for hours / coordinates / price level.
- Graceful fallback: if no API key, return `confidence: low` and let the LLM estimate.

#### 6e. `tools/transport_lookup.py`
- v1: curated JSON of common routes (Shinkansen, major flight corridors). Look up by `(origin_city, dest_city)`.
- Mark anything not in the table as `confidence: medium` and let the agent fill in via web search.

### Deliverables
- Plans reference real POIs with real-ish hours and coordinates. Transport legs cite a concrete mode.

### Acceptance
- Each tool has a unit test using a recorded fixture (no live network in tests).
- Integration test: Japan run completes successfully with all tools enabled and again with all tools disabled (LLM-only fallback path).

### Exit criteria — M4 ships
- Plan quality is noticeably better. Cost per run logged. We can compare with-tools vs. without-tools quality.

---

## Phase 7 — UX & Clarification Loop (1–2 days)

**Scope:** Architecture milestone **M5**. Make the system usable by a non-developer.

### Tasks
1. When Intent emits non-empty `open_questions`, the orchestrator returns to the UI **before** running specialists. UI surfaces questions, takes answers, re-runs Intent with the appended context.
2. `ui/streamlit_app.py` — single-page app:
   - Text input for the request.
   - "Plan my trip" button.
   - Streaming progress per agent (Intent → Destination → ...).
   - Final plan rendered with collapsible sections (Itinerary, Stays, Transport, Budget, Constraint-fit summary).
   - Sidebar: trace viewer (which agent ran when, cost, latency).
3. `ui/cli.py` — same flow, simpler rendering, supports `--trace` flag to print the full timeline.

### Deliverables
- Non-technical user can plan a trip in the browser.

### Acceptance
- All 5 fixture requests run successfully through the Streamlit app.
- Underspecified fixture surfaces clarifying questions.

### Exit criteria — M5 ships
- Demo-ready. Stop here for v1.

---

## Phase 8 — Observability, Cost, & Quality Tracking (1 day)

**Scope:** Make the system measurable, not just runnable. Spans across phases — start in Phase 0, lock in here.

### Tasks
1. Per-trip summary in trace: total tokens, total $, total wall time, critic verdict, revision count.
2. `scripts/quality_report.py` — reads `traces/*.jsonl`, prints aggregate: critic-pass rate, mean cost, mean latency, top failure rules.
3. Add `pytest -m "live"` suite that runs the Japan example and asserts critic-pass — gated by `RUN_LIVE=1` env var so it doesn't spend tokens in normal CI.

### Deliverables
- A single command answers "is the system getting better or worse this week?"

### Acceptance
- `python scripts/quality_report.py` produces a readable summary on a directory of traces.

### Exit criteria
- We can iterate on prompts/tools with a feedback loop, not by eyeballing outputs.

---

## Phase 9 — v2 Roadmap (post-v1, do not block v1 ship)

Per architecture §9 (M6) and Appendix B. Not scheduled — captured here so the codebase is built with these in mind:

- **Cross-session memory** — user profile model, persistent preferences. Requires auth.
- **Real bookings** — flights, hotels via a booking API. Requires payment + legal review.
- **Multi-traveler constraint solving** — group preference reconciliation.
- **Caching layer** for Destination Research keyed by `(city, normalized-prefs-hash)`.
- **Itinerary Builder split** — Day-Builder + Sequencer if single-agent quality plateaus.
- **Critic with tools** — let it verify hours / distances directly. Decide based on M3 metrics.

---

## Cross-Cutting Concerns (apply to every phase)

### Testing strategy
- **Unit:** schemas, tools, deterministic critic rules.
- **Integration:** one full run per fixture, mocked LLM responses for determinism.
- **Live (gated):** real LLM + real tools, runs nightly or on demand. Spend cap per run.

### Prompt engineering discipline
- Every agent prompt lives in its own `*.py` file as a constant — not in a YAML, not built at runtime — so prompt diffs are reviewable.
- Every prompt change requires re-running the live suite and updating the quality report.

### Failure handling (architecture §7.2)
- Wire the four failure modes (ambiguous intent, budget overrun, critic-fails-twice, tool timeout) **as the orchestrator is built**, not retrofitted.
- Each failure mode has at least one test.

### Cost control
- Hard cap per run (e.g., $0.50). Orchestrator aborts if exceeded.
- Gemini Flash for Intent + Critic, Gemini Pro for the rest, per architecture §8.1.

---

## Suggested Schedule

Assuming one developer working at a steady pace:

| Phase | Effort | Cumulative |
|-------|--------|-----------|
| 0 — Bootstrap | ½ day | 0.5 |
| 1 — Schemas | ½ day | 1.0 |
| 2 — Intent + CLI | 1 day | 2.0 |
| 3 — Skeleton plan (M1) | 1 day | 3.0 |
| 4 — Specialists (M2) | 2–3 days | 5.5 |
| 5 — Critic loop (M3) | 1–2 days | 7.0 |
| 6 — Tools (M4) | 2–3 days | 9.5 |
| 7 — UX (M5) | 1–2 days | 11.0 |
| 8 — Observability | 1 day | 12.0 |

**~2 calendar weeks to v1 demo.** Phases 4a–4d and most of phase 6 sub-tasks can be parallelized across multiple developers if available.

---

## Definition of Done (v1)

The project is shippable when **all** of these hold:
1. The Japan example produces a critic-passing plan in ≥ 80% of runs.
2. All 5 fixture requests run end-to-end without crashes.
3. Trace files capture every agent invocation with cost and latency.
4. `quality_report.py` produces a clean summary on a fresh trace directory.
5. A user with no codebase context can run the Streamlit app, enter a request, and read a usable plan.
6. README documents setup, how to run, how to add a new agent, and known limitations.
