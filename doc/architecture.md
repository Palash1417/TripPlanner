# Travel Planning Multi-Agent System — Phase-Wise Architecture

> Reference: [problemStatement.md](problemStatement.md)

A specialized multi-agent system that turns a short natural-language travel request into a complete, budget-aware, preference-respecting itinerary. This document describes the architecture in **9 phases**, moving from raw user input to a validated final plan.

---

## High-Level Overview

```
   ┌──────────────────────────────────────────────────────────────────┐
   │                      USER (Natural Language)                     │
   │   "Plan a 5-day trip to Japan. Tokyo + Kyoto. $3000. Food/temples"│
   └───────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │                   ORCHESTRATOR / PLANNER AGENT                   │
   │              (Routes, sequences, aggregates, retries)            │
   └─┬────────┬─────────┬────────────┬──────────┬─────────┬──────────┘
     │        │         │            │          │         │
     ▼        ▼         ▼            ▼          ▼         ▼
   Intent   Destination Accommodation Transport Budget   Itinerary
   Agent    Research    Agent         Agent     Agent    Builder
            Agent                                        Agent
                                                            │
                                                            ▼
                                                    ┌────────────────┐
                                                    │  Critic Agent  │
                                                    │ (validation)   │
                                                    └────────┬───────┘
                                                             │
                                                             ▼
                                              ┌──────────────────────────┐
                                              │  Final Trip Plan (JSON + │
                                              │  Markdown render to user)│
                                              └──────────────────────────┘
```

---

## Phase 1 — Requirements & Input Contract

**Goal:** Lock down what the system accepts and what it produces before designing internals.

### 1.1 Input
- Free-form natural-language travel request (single string, no structured fields).
- May include: destinations, duration, budget, preferences, dislikes, traveler count.
- May omit: dates, traveler count, exact cities, currency.

### 1.2 Output (final artifact)
- **Day-by-day itinerary** (each day → activities, meals, neighborhoods).
- **Stay recommendations** (neighborhood + ~2 properties/day with price).
- **Inter-city transport** plan (mode, est. duration, est. cost).
- **Budget breakdown** (lodging, food, transport, activities, buffer).
- **Constraint-fit summary** (which prefs honored, which trade-offs made).

### 1.3 Non-Goals (v1)
- Live booking / payments.
- Real-time price guarantees.
- Visa, insurance, vaccinations.
- Multi-traveler constraint solving (group polling, etc.).

### 1.4 Quality Bar
- Every recommendation must trace back to a stated preference or constraint.
- Total estimated cost must be ≤ user budget (or call out the overage explicitly).
- Itinerary must be physically feasible (no Tokyo-breakfast → Kyoto-lunch → Tokyo-dinner).

---

## Phase 2 — Agent Roster & Responsibilities

**Goal:** Define each specialist with a single, sharp responsibility. Each agent is an LLM with a system prompt + a tool subset.

| Agent | Single Responsibility | Inputs | Outputs |
|-------|----------------------|--------|---------|
| **Intent Agent** | Parse free-form request into structured `TripBrief` | raw user text | `TripBrief` (JSON) |
| **Destination Research Agent** | Find attractions / neighborhoods matching prefs | `TripBrief` | ranked POI list per city |
| **Accommodation Agent** | Recommend stay neighborhoods + sample properties | `TripBrief`, POI list | stay options w/ price band |
| **Transport Agent** | Plan inter-city + intra-city movement | cities, dates, budget | transport legs w/ cost/duration |
| **Budget Agent** | Allocate budget across categories, flag overruns | all cost estimates | budget table + warnings |
| **Itinerary Builder Agent** | Stitch components into a feasible day-by-day plan | all of the above | draft itinerary |
| **Critic Agent** | Validate the draft against `TripBrief` and feasibility rules | draft + brief | pass / fail + targeted fixes |
| **Orchestrator** | Route, retry, aggregate, decide when "done" | all agent outputs | final plan |

### 2.1 Why this split
- **Separation of concerns** → each prompt stays small and focused.
- **Independent improvement** → can swap models/tools per agent (e.g., a smaller model for Intent, a stronger one for Itinerary).
- **Parallelism** → Destination, Accommodation, Transport can run concurrently after Intent.

---

## Phase 3 — Data Contracts (Shared Schemas)

**Goal:** Agents communicate through typed JSON, not free text. Every agent has a strict input/output schema.

### 3.1 `TripBrief` (output of Intent Agent)
```json
{
  "origin": "string | null",
  "destinations": ["Tokyo", "Kyoto"],
  "duration_days": 5,
  "budget": {"amount": 3000, "currency": "USD"},
  "travelers": {"adults": 1, "children": 0},
  "preferences": {
    "likes": ["food", "temples"],
    "dislikes": ["crowds"],
    "pace": "moderate",
    "accessibility": []
  },
  "dates": {"start": null, "end": null, "flexible": true},
  "open_questions": ["dates not provided"]
}
```

### 3.2 `POI` (Destination Research Agent)
```json
{
  "name": "Fushimi Inari Shrine",
  "city": "Kyoto",
  "category": "temple",
  "match_reasons": ["likes:temples", "dislikes:crowds → visit early AM"],
  "est_visit_minutes": 120,
  "est_cost_usd": 0,
  "tags": ["sunrise-friendly", "outdoor"]
}
```

### 3.3 `StayOption`, `TransportLeg`, `BudgetLine`, `ItineraryDay`
Each follows the same pattern: explicit fields, every recommendation tagged with `match_reasons` linking back to `TripBrief`.

### 3.4 Why typed contracts
- The Critic can mechanically check `match_reasons` cover the user's stated likes/dislikes.
- The Orchestrator can validate schema before passing data downstream — catches malformed LLM output early.

---

## Phase 4 — Orchestration & Workflow

**Goal:** Define the control flow that turns a `TripBrief` into a validated plan.

### 4.1 Execution graph
```
   Intent
     │
     ▼
  TripBrief ───┬───────────────┬───────────────┐
               ▼               ▼               ▼
         Destination     Accommodation     Transport
         Research          Agent            Agent
               │               │               │
               └───────┬───────┴───────┬───────┘
                       ▼               ▼
                   Budget Agent (rebalances if overrun)
                       │
                       ▼
              Itinerary Builder
                       │
                       ▼
                  Critic Agent ──── fail ──► targeted re-run of guilty agent(s)
                       │
                      pass
                       ▼
                  Final Plan
```

### 4.2 Orchestrator responsibilities
1. **Schema validation** at every boundary (reject malformed agent output → retry).
2. **Parallel dispatch** for independent steps (Destination ‖ Accommodation ‖ Transport).
3. **Critic loop** with a hard cap (e.g., max 2 revision rounds → otherwise return best-effort + warnings).
4. **Cost ceiling** — abort if total LLM/tool spend exceeds threshold.
5. **Tracing** — assign a `trip_id` and log every agent call against it.

### 4.3 Why a critic loop (and not just one shot)
- LLMs hallucinate plausible but infeasible plans (wrong city order, double-counted budget).
- A separate Critic with **only validation rules in its prompt** catches these before they reach the user.

---

## Phase 5 — Tools & External Data

**Goal:** Decide what each agent can call. Keep the tool surface small in v1.

| Tool | Used by | Purpose | v1 source |
|------|---------|---------|-----------|
| `search_web` | Destination, Accommodation | POIs, neighborhood guides | web search API |
| `get_place_details` | Destination | hours, price, location | Maps API (or LLM-only fallback) |
| `geo_distance` | Itinerary, Transport | feasibility check between activities | local Haversine util |
| `currency_convert` | Budget | normalize to user's currency | static rates table |
| `transport_lookup` | Transport | rough train/flight times + price bands | curated JSON / web search |
| `vector_search` | Destination | retrieve from a curated POI knowledge base | local embeddings (optional) |

### 5.1 v1 simplification
Live booking APIs are out of scope. Use **estimated price bands** and clearly label outputs as estimates. This keeps the system testable without API keys for every domain.

### 5.2 Tool authorship principle
Each tool returns structured data — never raw HTML. If using web search, an extraction step normalizes results before the agent sees them, otherwise the agent burns context on noise.

---

## Phase 6 — Memory & State

**Goal:** Decide what persists, where, and for how long.

### 6.1 Three-tier state model
1. **Run-scoped state** (`TripContext`) — the shared blackboard for one planning run. Holds `TripBrief`, partial agent outputs, decision log. Lives in memory; written to disk per `trip_id` for replay/debugging.
2. **Session memory** — recent user clarifications within a single chat session (e.g., "actually make it 4 days").
3. **Knowledge memory (optional v2)** — a vector store of curated city/POI notes the Destination Agent can retrieve from. Reduces reliance on web search.

### 6.2 What we deliberately do NOT store (v1)
- User PII beyond what they typed.
- Long-term cross-session preferences (would require auth + a profile model).

### 6.3 Why this scope
v1 is request → plan, not a personal assistant. Cross-session memory is a v2 concern once we have real users.

---

## Phase 7 — Validation, Quality, & Failure Modes

**Goal:** Specify how the system knows the plan is good.

### 7.1 Critic Agent rules (deterministic where possible)
- **Budget rule** — `sum(BudgetLine.estimate) ≤ TripBrief.budget.amount`.
- **Coverage rule** — every `preferences.like` appears in ≥ 1 `match_reasons` across the plan.
- **Avoidance rule** — no recommendation tagged with a `preferences.dislike` without an explicit mitigation.
- **Geographic feasibility** — no day's activities span > X km without a planned transport leg.
- **Day balance** — `sum(activity.minutes) per day ≤ realistic ceiling` (e.g., 10 hrs).

### 7.2 Failure modes & responses
| Failure | Detection | Response |
|---------|-----------|----------|
| Intent ambiguous | Intent Agent emits `open_questions` | Orchestrator surfaces a clarifying question to user |
| Budget overrun | Budget Agent flag | Re-run Accommodation/Itinerary with tighter band |
| Critic fails twice | revision counter | Return best plan + explicit warnings list |
| Tool timeout | tool wrapper | Fall back to LLM-only estimate, mark field `confidence: low` |

### 7.3 Why a separate Critic vs. self-check
A self-checking agent rarely catches its own hallucinations. A Critic with a different prompt and only the rules above is empirically much stricter.

---

## Phase 8 — Tech Stack, Deployment & Observability

**Goal:** Pick boring, debuggable defaults.

### 8.1 Stack (proposed)
- **Language:** Python 3.11+.
- **Agent framework:** LangGraph **or** a thin custom orchestrator (state machine over agent nodes). Avoid heavy frameworks if a 200-line orchestrator suffices.
- **LLM:** Google Gemini free tier (Flash for Intent/Critic, Pro for the heavier agents). Provider-agnostic `LLMClient` wraps the SDK so swapping models/providers is a single-file change.
- **Schema validation:** Pydantic for every contract in Phase 3.
- **Storage:** local JSON files keyed by `trip_id` for v1; SQLite when persistence matters.
- **Frontend (v1):** CLI or minimal Streamlit for demoing.

### 8.2 Observability (mandatory from day one)
- Per-agent **trace log**: input, output, latency, token cost.
- A single `trip_id` threads through all logs → one trip = one viewable timeline.
- Critic verdicts logged separately so we can audit failure rate over time.

### 8.3 Why these choices
- Pydantic + JSON contracts make agent failures debuggable without reading prompts.
- Per-agent traces are the only way to localize "the plan is bad" to a specific agent.

---

## Phase 9 — Iteration Roadmap

**Goal:** Sequence the build so each phase ships something demoable.

| Milestone | Scope | Demo |
|-----------|-------|------|
| **M1 — Skeleton** | Intent → Itinerary Builder, no Critic, no tools, LLM-only | Hard-coded request returns a plausible plan |
| **M2 — Specialists** | Add Destination, Accommodation, Transport, Budget agents | Plan now uses real(ish) POIs and prices |
| **M3 — Critic loop** | Add validation + revision loop | Plans now respect budget + preference coverage |
| **M4 — Tools** | Web search + maps integration | Recommendations feel less generic |
| **M5 — UX** | Streamlit/CLI with clarifying questions | End-to-end demo on novel requests |
| **M6 (v2)** | Cross-session memory, real bookings, multi-traveler | Out of v1 scope |

---

## Appendix A — Example End-to-End Trace

**Input:** "Plan a 5-day trip to Japan. Tokyo + Kyoto. $3,000 budget. Love food and temples, hate crowds."

1. **Intent Agent** → `TripBrief` with `likes:[food, temples]`, `dislikes:[crowds]`, `duration:5`, `budget:3000 USD`.
2. **Parallel fan-out:**
   - Destination → ranked POIs, each tagged with `early-AM` for crowd avoidance.
   - Accommodation → Asakusa (Tokyo) + Higashiyama (Kyoto), price band $120–180/night.
   - Transport → Shinkansen Tokyo↔Kyoto, ~$130 each way, 2h15m.
3. **Budget Agent** → lodging $750, transport $260, food $500, activities $200, buffer $290 → fits $3000.
4. **Itinerary Builder** → 3 days Tokyo, 2 days Kyoto, transit on day 4 morning, temple visits scheduled 6:30–9:00 AM.
5. **Critic** → all `likes` covered, no dislikes triggered, geo-feasible, under budget → **pass**.
6. **Final Plan** → returned to user as Markdown + structured JSON.

---

## Appendix B — Open Design Decisions

These are deliberately unresolved and should be revisited after M2:
- Does the **Critic** call tools, or does it only see what other agents produced? (Leaning: no tools — keeps it cheap and deterministic.)
- Should **Itinerary Builder** be one agent or split into Day-Builder + Sequencer?
- Cache layer for **Destination Research** — same city queried repeatedly by different users.
- How aggressively should the orchestrator **re-plan** vs. patch on Critic failure?
