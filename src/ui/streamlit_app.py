"""Phase 7 Streamlit UI — single-page trip planner with live per-agent progress.

Run with:
    streamlit run src/ui/streamlit_app.py
"""

from __future__ import annotations

import html
import os
import sys
import time

# Ensure the project root is on sys.path (Streamlit runs the file directly).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import streamlit as st  # noqa: E402

# Bridge Streamlit Cloud secrets into os.environ so LLMClient (which reads
# os.getenv) works in cloud deploys without code changes. Local .env wins
# because load_dotenv() ran above; we only fill blanks.
for _k in ("GEMINI_API_KEY", "GEMINI_MODEL_FAST", "GEMINI_MODEL_SMART"):
    if not os.getenv(_k):
        try:
            if _k in st.secrets:
                os.environ[_k] = str(st.secrets[_k])
        except (FileNotFoundError, KeyError):
            pass

from src.agents import (  # noqa: E402
    accommodation,
    budget as budget_agent,
    critic,
    destination,
    intent,
    itinerary,
    transport,
)
from src.llm import LLMClient, QuotaExhaustedError  # noqa: E402
from src.orchestrator import TripContext  # noqa: E402
from src.orchestrator.graph import _MAX_REVISIONS, _retarget  # noqa: E402
from src.tools import currency as currency_tool, web_search  # noqa: E402


# ---------- display-currency helpers ----------
#
# Internal accounting is in USD (schema fields end in `_usd`). The UI converts
# to whatever currency the user named in their brief — falling back to USD if
# they didn't name one. Conversion uses the static rates in tools/currency.py.

_CCY_SYMBOL = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹",
}


def _display_ccy(brief) -> str:
    if brief and brief.budget and brief.budget.currency:
        return brief.budget.currency.upper()
    return "USD"


def _fmt_money(amount_usd: float, ccy: str) -> str:
    """Convert a USD amount to `ccy` and format it with the right symbol/code."""
    try:
        converted = currency_tool.convert(amount_usd, "USD", ccy)
    except ValueError:
        converted = amount_usd
        ccy = "USD"
    sym = _CCY_SYMBOL.get(ccy)
    body = f"{converted:,.0f}"
    return f"{sym}{body}" if sym else f"{body} {ccy}"


# ---------- page config ----------

st.set_page_config(
    page_title="WanderAI — Plan smarter trips",
    page_icon="🧳",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------- visual theme (OpenTrip-inspired dark landing) ----------
#
# All custom styling lives in this single block so designers can tune the look
# without touching pipeline code. The hero mimics a moody dark-photo landing
# page with a serif gold headline and rounded "destination" cards on the right;
# the form/results below keep a lighter, card-driven layout.

_THEME_CSS = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap');

  :root {
    --gold: #d6b46a;
    --gold-soft: #e9cf94;
    --ink-on-dark: #f5f1e8;
    --ink: #1f2937;
    --ink-muted: #6b7280;
    --bg: #0e0d0c;
    --bg-soft: #f6f7fb;
    --card: #ffffff;
    --border: #e5e7eb;
    --shadow-sm: 0 1px 2px rgba(15,23,42,0.06);
    --shadow-md: 0 6px 16px rgba(15,23,42,0.08);
    --shadow-lg: 0 12px 32px rgba(15,23,42,0.12);
    --serif: 'Cormorant Garamond', 'Playfair Display', Georgia, serif;
    --sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }

  .stApp { background: var(--bg-soft); font-family: var(--sans); }

  /* Streamlit's default top padding eats too much real estate for the hero. */
  .block-container { padding-top: 0.4rem !important; padding-bottom: 4rem; max-width: 1240px; }

  /* ---- Top nav pill ---- */
  .ot-nav { display: flex; justify-content: center; padding: 18px 0 12px; }
  .ot-nav-pill {
    display: flex; align-items: center; gap: 28px;
    background: rgba(20,18,16,0.78);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 999px;
    padding: 12px 28px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.35);
  }
  .ot-nav-pill .ot-logo {
    font-family: var(--serif); color: var(--gold);
    font-size: 1.05rem; font-style: italic; font-weight: 600;
    letter-spacing: 0.01em; padding-right: 6px;
  }
  .ot-nav-pill a {
    color: #d8d2c4; text-decoration: none; font-size: 0.9rem;
    font-weight: 400; transition: color .2s;
  }
  .ot-nav-pill a:hover { color: white; }
  .ot-nav-pill .ot-signin { margin-left: 26px; color: #ece6d8; }

  /* ---- Dark hero ---- */
  .ot-hero {
    position: relative;
    border-radius: 22px;
    overflow: hidden;
    margin-bottom: 28px;
    min-height: 520px;
    background:
      linear-gradient(110deg, rgba(8,8,10,0.85) 0%, rgba(15,12,10,0.55) 55%, rgba(15,12,10,0.35) 100%),
      radial-gradient(ellipse at 65% 40%, #6a4e3a 0%, #2a201b 45%, #0e0b09 100%);
    box-shadow: 0 18px 60px rgba(0,0,0,0.55);
    color: var(--ink-on-dark);
    padding: 48px 56px 56px;
    display: grid;
    grid-template-columns: 1.15fr 1fr;
    gap: 32px;
    align-items: center;
  }
  .ot-hero::before {
    /* moody atmospheric vignette */
    content: ""; position: absolute; inset: 0;
    background:
      radial-gradient(circle at 50% 60%, rgba(214,180,106,0.08), transparent 55%),
      linear-gradient(180deg, transparent 60%, rgba(0,0,0,0.55) 100%);
    pointer-events: none;
  }
  .ot-hero-copy { position: relative; z-index: 2; max-width: 540px; }
  .ot-hero-copy h1 {
    font-family: var(--serif);
    font-weight: 600;
    font-size: 4.2rem;
    line-height: 1.02;
    letter-spacing: -0.01em;
    color: var(--gold-soft);
    margin: 0 0 22px;
    text-shadow: 0 2px 24px rgba(0,0,0,0.55);
  }
  .ot-hero-copy p {
    font-size: 1.05rem; line-height: 1.55;
    color: #e7e1d3; opacity: 0.92;
    margin: 0 0 36px; max-width: 440px;
  }
  .ot-cta-row { display: flex; gap: 14px; flex-wrap: wrap; }
  .ot-cta {
    display: inline-flex; align-items: center; gap: 14px;
    background: rgba(20,16,12,0.55);
    border: 1px solid rgba(245,241,232,0.35);
    color: var(--ink-on-dark);
    padding: 14px 22px;
    border-radius: 999px;
    font-size: 0.98rem;
    font-family: var(--serif); font-style: italic; font-weight: 500;
    text-decoration: none;
    backdrop-filter: blur(8px);
    transition: all .25s ease;
  }
  .ot-cta:hover {
    background: rgba(214,180,106,0.18);
    border-color: var(--gold);
    color: white;
    transform: translateY(-1px);
  }
  .ot-cta .ot-icon {
    display: inline-flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; border-radius: 50%;
    border: 1px solid rgba(245,241,232,0.55);
    font-size: 0.9rem; font-style: normal;
  }

  /* ---- Destination cards on the right of the hero ---- */
  .ot-cards {
    position: relative; z-index: 2;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 22px 26px;
    justify-self: end;
    align-self: center;
  }
  .ot-card {
    width: 150px; height: 150px;
    border-radius: 22px;
    position: relative;
    overflow: hidden;
    box-shadow: 0 12px 28px rgba(0,0,0,0.45);
    backdrop-filter: blur(2px);
    border: 1px solid rgba(255,255,255,0.08);
    display: flex; align-items: flex-end; justify-content: center;
    padding-bottom: 14px;
  }
  .ot-card::before {
    content: ""; position: absolute; inset: 0;
    filter: blur(14px) saturate(0.85);
    transform: scale(1.15);
  }
  .ot-card::after {
    content: ""; position: absolute; inset: 0;
    background: linear-gradient(180deg, rgba(0,0,0,0) 55%, rgba(0,0,0,0.55) 100%);
  }
  .ot-card .ot-label {
    position: relative; z-index: 2;
    color: white;
    font-family: var(--serif);
    font-size: 1.05rem;
    letter-spacing: 0.02em;
    text-shadow: 0 2px 8px rgba(0,0,0,0.6);
  }
  .ot-card.japan::before  { background: linear-gradient(135deg,#c9b9a4,#7a6856 60%,#3b2e25); }
  .ot-card.brazil::before { background: linear-gradient(135deg,#d4c298,#9c7a4b 55%,#3e2a18); }
  .ot-card.france::before { background: linear-gradient(135deg,#b9b3a4,#766a55 60%,#2f2a22); }
  .ot-card.thailand::before { background: linear-gradient(135deg,#cbbfa6,#7e6a52 60%,#352920); }

  @media (max-width: 1000px) {
    .ot-hero { grid-template-columns: 1fr; padding: 36px 28px; min-height: auto; }
    .ot-hero-copy h1 { font-size: 2.8rem; }
    .ot-cards { justify-self: center; }
  }

  /* ---- Cards / generic surfaces ---- */
  .wa-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px 20px;
    box-shadow: var(--shadow-sm);
  }
  .wa-card + .wa-card { margin-top: 12px; }
  .wa-card h3 { margin: 0 0 6px; font-size: 1.05rem; color: var(--ink); }
  .wa-card .wa-sub { color: var(--ink-muted); font-size: .9rem; }

  .wa-section-title {
    font-weight: 700; color: var(--ink); font-size: 1.15rem;
    margin: 6px 0 10px; display: flex; align-items: center; gap: 10px;
  }
  .wa-section-title .wa-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--brand-orange);
  }

  /* ---- Trip-overview banner card ---- */
  .wa-overview {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 14px; margin-bottom: 14px;
  }
  .wa-stat {
    background: white; border: 1px solid var(--border); border-radius: 14px;
    padding: 14px 16px; box-shadow: var(--shadow-sm);
  }
  .wa-stat .wa-stat-label {
    font-size: .78rem; text-transform: uppercase; letter-spacing: .05em;
    color: var(--ink-muted); font-weight: 600;
  }
  .wa-stat .wa-stat-value {
    font-size: 1.35rem; color: var(--ink); font-weight: 700; margin-top: 2px;
  }
  .wa-stat.accent { border-top: 3px solid var(--brand-orange); }

  /* ---- Tabs ---- */
  div[data-baseweb="tab-list"] {
    background: white; padding: 6px; border-radius: 12px;
    border: 1px solid var(--border); box-shadow: var(--shadow-sm); gap: 4px;
  }
  button[data-baseweb="tab"] {
    border-radius: 9px !important; padding: 8px 16px !important;
    color: var(--ink-muted) !important; font-weight: 600 !important;
  }
  button[data-baseweb="tab"][aria-selected="true"] {
    background: linear-gradient(135deg, #ff8a3d, #ff5a2c) !important;
    color: white !important; box-shadow: var(--shadow-sm);
  }
  div[data-baseweb="tab-highlight"] { display: none; }

  /* ---- Itinerary timeline ---- */
  .wa-day {
    background: white; border: 1px solid var(--border); border-radius: 14px;
    padding: 16px 18px; margin-bottom: 12px; box-shadow: var(--shadow-sm);
    position: relative;
  }
  .wa-day-head {
    display: flex; align-items: center; gap: 12px; margin-bottom: 8px;
  }
  .wa-day-badge {
    background: linear-gradient(135deg, #1a73e8, #0b5cc7);
    color: white; font-weight: 700; padding: 6px 12px;
    border-radius: 999px; font-size: .8rem; letter-spacing: .03em;
  }
  .wa-day-city { font-size: 1.1rem; font-weight: 700; color: var(--ink); }
  .wa-day-meta { color: var(--ink-muted); font-size: .85rem; }
  .wa-act {
    border-left: 3px solid var(--brand-orange); padding: 2px 12px;
    margin: 8px 0; display: flex; gap: 14px; align-items: baseline;
  }
  .wa-act .wa-time {
    font-weight: 700; color: var(--brand-blue-dark); min-width: 56px;
  }
  .wa-act .wa-title { font-weight: 600; color: var(--ink); }
  .wa-act .wa-meta { color: var(--ink-muted); font-size: .85rem; }
  .wa-transit {
    background: linear-gradient(135deg, #eaf2ff, #f6ecff);
    border: 1px dashed #bcd0f5; padding: 10px 14px; border-radius: 10px;
    color: var(--ink); margin-bottom: 8px; font-size: .92rem;
  }

  /* ---- Stay / transport / POI cards ---- */
  .wa-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 14px;
  }
  .wa-tile {
    background: white; border: 1px solid var(--border); border-radius: 14px;
    padding: 16px; box-shadow: var(--shadow-sm);
    display: flex; flex-direction: column; gap: 6px;
  }
  .wa-tile .wa-tile-head {
    display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;
  }
  .wa-tile .wa-name { font-weight: 700; color: var(--ink); font-size: 1.02rem; }
  .wa-tile .wa-where { color: var(--ink-muted); font-size: .85rem; }
  .wa-tile .wa-price {
    color: var(--brand-orange-dark); font-weight: 700; white-space: nowrap;
  }
  .wa-chip {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: .72rem; font-weight: 600; background: #fff1ea;
    color: var(--brand-orange-dark); border: 1px solid #ffd9c4;
  }
  .wa-chip.blue {
    background: #e8f1ff; color: var(--brand-blue-dark); border-color: #c7dcff;
  }
  .wa-chip.green {
    background: #e7f7ed; color: #1f8a4c; border-color: #b9e5cb;
  }
  .wa-chip.gray {
    background: #f1f3f7; color: var(--ink-muted); border-color: var(--border);
  }
  .wa-tile .wa-why {
    color: var(--ink-muted); font-size: .85rem; font-style: italic;
  }

  /* ---- Budget bar ---- */
  .wa-bar-track {
    background: #eef0f5; border-radius: 999px; height: 16px; overflow: hidden;
    border: 1px solid var(--border);
  }
  .wa-bar-fill {
    height: 100%; background: linear-gradient(90deg, #ff8a3d, #ff5a2c);
    border-radius: 999px;
  }
  .wa-bar-fill.over { background: linear-gradient(90deg, #ec3b6a, #b91c5c); }
  .wa-bar-meta {
    display: flex; justify-content: space-between; color: var(--ink-muted);
    font-size: .85rem; margin-top: 6px;
  }

  /* ---- Verdict banner ---- */
  .wa-verdict {
    border-radius: 14px; padding: 14px 18px; margin-bottom: 10px;
    font-weight: 600;
  }
  .wa-verdict.pass {
    background: linear-gradient(135deg, #e7f7ed, #d2f0de);
    border: 1px solid #a9dcbe; color: #14693a;
  }
  .wa-verdict.fail {
    background: linear-gradient(135deg, #fff1ea, #ffd9c4);
    border: 1px solid #ffb796; color: #a8390f;
  }

  /* ---- Submit button ---- */
  .stForm button[kind="primary"], .stButton button[kind="primary"] {
    background: linear-gradient(135deg, #ff8a3d, #ff5a2c) !important;
    border: 0 !important; color: white !important;
    padding: 0.55rem 1.6rem !important; border-radius: 12px !important;
    font-weight: 700 !important; font-size: 1rem !important;
    box-shadow: var(--shadow-md);
  }
  .stForm button[kind="primary"]:hover, .stButton button[kind="primary"]:hover {
    transform: translateY(-1px); box-shadow: var(--shadow-lg);
  }

  /* ---- Open questions card ---- */
  .wa-questions {
    background: linear-gradient(135deg, #fff8e6, #fff1cf);
    border: 1px solid #f5d775; border-radius: 14px;
    padding: 16px 20px; margin-bottom: 12px;
    color: #5a4408;
  }
  .wa-questions h4 { margin: 0 0 6px; color: #7a5b0a; }
  .wa-questions ul { margin: 6px 0 0; padding-left: 20px; color: #5a4408; }
  .wa-questions li { color: #5a4408; font-size: .95rem; line-height: 1.5; margin: 2px 0; }

  /* Hide Streamlit chrome that breaks the polished look */
  #MainMenu, footer { visibility: hidden; }
</style>
"""


def _inject_theme() -> None:
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


_inject_theme()


# ---------- hero ----------

_data_mode = (
    "Web-grounded · Tavily/Serper/Brave"
    if web_search.is_available()
    else "LLM-only mode"
)

st.markdown(
    f"""
    <nav class="ot-nav">
      <div class="ot-nav-pill">
        <span class="ot-logo">OpenTrip</span>
        <a href="#trip-form">Trip Planner</a>
        <a href="#">Guide Deals</a>
        <a href="#">Destinations</a>
        <a href="#">About</a>
        <a class="ot-signin" href="#">Sign in</a>
      </div>
    </nav>

    <section class="ot-hero">
      <div class="ot-hero-copy">
        <h1>Plan your next<br/>adventure</h1>
        <p>Build your perfect trip and organize every detail in one place.</p>
        <div class="ot-cta-row">
          <a class="ot-cta" href="#trip-form">
            Create Your Trip <span class="ot-icon">→</span>
          </a>
          <a class="ot-cta" href="#trip-form">
            Explore our destinations <span class="ot-icon">⌕</span>
          </a>
        </div>
        <div style="margin-top:22px;font-size:.78rem;color:#bdb6a5;letter-spacing:.04em;">
          ⚡ Powered by Gemini · {_data_mode}
        </div>
      </div>
      <div class="ot-cards">
        <div class="ot-card japan"><span class="ot-label">Japan</span></div>
        <div class="ot-card brazil"><span class="ot-label">Brazil</span></div>
        <div class="ot-card france"><span class="ot-label">France</span></div>
        <div class="ot-card thailand"><span class="ot-label">Thailand</span></div>
      </div>
    </section>
    <div id="trip-form"></div>
    """,
    unsafe_allow_html=True,
)


# ---------- input form ----------

DEFAULT_REQUEST = (
    "Plan a 5-day trip to Japan. Tokyo + Kyoto. $3,000 budget. "
    "Love food and temples, hate crowds."
)

st.session_state.setdefault("pending_questions", None)
st.session_state.setdefault("pending_request", None)
st.session_state.setdefault("quota_exhausted_during_revision", False)

with st.container():
    st.markdown(
        '<div class="wa-section-title"><span class="wa-dot"></span>'
        "Where to next?</div>",
        unsafe_allow_html=True,
    )
    with st.form("trip_form"):
        user_request = st.text_area(
            "Describe your trip in plain English",
            value=DEFAULT_REQUEST,
            height=110,
            label_visibility="collapsed",
            help="Free-form: destinations, duration, budget, preferences, dislikes.",
        )
        col1, col2 = st.columns([1, 4])
        with col1:
            submitted = st.form_submit_button("✨  Plan my trip", type="primary")
        with col2:
            st.caption(
                "Tip: paste any rough ask. If something's missing the planner will "
                "ask follow-up questions before doing the full work."
            )


# ---------- runner ----------


def _run_pipeline(user_request: str) -> TripContext:
    """Run the full pipeline, updating st.status containers per agent.

    Phase 7 contract: if Intent surfaces open_questions, halt BEFORE specialists
    so the user can answer them — we won't fabricate origin/budget/currency.
    Caller checks `context.brief.open_questions` to detect the halt.
    """
    context = TripContext(user_request=user_request)
    client = LLMClient()

    with st.status("🧠 Parsing your request (Intent)...", expanded=False) as box:
        intent.run(context, client=client)
        box.update(
            label=(
                f"✅ Intent — {len(context.brief.destinations)} destination(s), "
                f"{len(context.brief.open_questions)} open question(s)"
            ),
            state="complete",
        )

    if context.brief.open_questions:
        return context  # halt; UI will surface the questions and ask for refinement

    # Specialists (sequential — free-tier 5 RPM cap)
    for label, fn, agent_name in [
        ("🗺️ Researching destinations", destination.run, "destination"),
        ("🏨 Finding accommodation", accommodation.run, "accommodation"),
        ("🚄 Planning transport", transport.run, "transport"),
    ]:
        with st.status(f"{label}...", expanded=False) as box:
            fn(context, client=client)
            box.update(label=f"✅ {agent_name.title()} done", state="complete")

    with st.status("💰 Building budget...", expanded=False) as box:
        budget_agent.run(context, client=client)
        ccy = _display_ccy(context.brief)
        box.update(
            label=f"✅ Budget — {_fmt_money(context.budget.total_estimate_usd, ccy)}",
            state="complete",
        )

    with st.status("📅 Composing itinerary (revision 0)...", expanded=False) as box:
        itinerary.run(context, client=client)
        box.update(
            label=f"✅ Itinerary — {len(context.itinerary.days)} day(s)",
            state="complete",
        )

    # Critic + revision loop
    with st.status("🔍 Critic — validating plan...", expanded=False) as box:
        verdict = critic.run(context, client=client)
        box.update(
            label=(
                "✅ Critic passed on first try"
                if verdict.passed
                else f"⚠️ Critic found {len(verdict.violations)} violation(s)"
            ),
            state="complete",
        )

    for rev in range(1, _MAX_REVISIONS + 1):
        if verdict.passed:
            break
        with st.status(f"🔁 Revision {rev}: re-running guilty agents...", expanded=False) as box:
            context.current_revision = rev
            try:
                _retarget(context, verdict, client=client)
                verdict = critic.run(context, client=client)
            except QuotaExhaustedError:
                # Daily quota tripped during revision — surface a warning and
                # render the pre-revision plan, which is already complete.
                box.update(
                    label=(
                        f"⚠️ Revision {rev} skipped — Gemini daily quota exhausted; "
                        f"showing pre-revision plan"
                    ),
                    state="error",
                )
                st.session_state.quota_exhausted_during_revision = True
                break
            box.update(
                label=(
                    f"✅ Revision {rev} passed"
                    if verdict.passed
                    else f"⚠️ Revision {rev}: {len(verdict.violations)} violation(s)"
                ),
                state="complete",
            )

    return context


# ---------- result rendering ----------


def _esc(value) -> str:
    """Escape user/model text before injecting into HTML."""
    return html.escape("" if value is None else str(value), quote=True)


def _stat(label: str, value: str, *, accent: bool = False) -> str:
    cls = "wa-stat accent" if accent else "wa-stat"
    return (
        f'<div class="{cls}"><div class="wa-stat-label">{_esc(label)}</div>'
        f'<div class="wa-stat-value">{_esc(value)}</div></div>'
    )


def _render_brief(brief) -> None:
    dests = ", ".join(brief.destinations) if brief.destinations else "TBD"
    days = str(brief.duration_days or "?")
    budget_str = (
        f"{brief.budget.amount:,.0f} {brief.budget.currency}"
        if brief.budget else "—"
    )
    travelers_str = f"{brief.travelers.adults} adult"
    if brief.travelers.adults != 1:
        travelers_str += "s"
    if brief.travelers.children:
        travelers_str += f" + {brief.travelers.children} child"
        if brief.travelers.children != 1:
            travelers_str += "ren"

    st.markdown(
        f"""
        <div class="wa-overview">
          {_stat("Destinations", dests, accent=True)}
          {_stat("Days", days)}
          {_stat("Budget", budget_str)}
          {_stat("Travelers", travelers_str)}
        </div>
        """,
        unsafe_allow_html=True,
    )

    likes = ", ".join(brief.preferences.likes) if brief.preferences.likes else "—"
    dislikes = (
        ", ".join(brief.preferences.dislikes) if brief.preferences.dislikes else "—"
    )
    pace = brief.preferences.pace or "—"
    chips = (
        f'<span class="wa-chip">❤️ Likes: {_esc(likes)}</span>'
        f'<span class="wa-chip blue">🚫 Avoid: {_esc(dislikes)}</span>'
        f'<span class="wa-chip gray">⏱️ Pace: {_esc(pace)}</span>'
    )
    st.markdown(
        f'<div class="wa-card"><div style="display:flex;flex-wrap:wrap;gap:8px;">'
        f"{chips}</div></div>",
        unsafe_allow_html=True,
    )

    with st.expander("Brief details (raw JSON)", expanded=False):
        st.json(brief.model_dump(mode="json"))


def _conf_chip(confidence: str) -> str:
    cls = {
        "high": "green",
        "medium": "blue",
        "low": "gray",
    }.get(confidence, "gray")
    return f'<span class="wa-chip {cls}">{_esc(confidence)} confidence</span>'


def _render_destinations(catalog, ccy: str) -> None:
    st.markdown(
        '<div class="wa-section-title"><span class="wa-dot"></span>'
        "Things to do</div>",
        unsafe_allow_html=True,
    )
    tiles: list[str] = []
    for p in catalog.pois:
        cost = (
            _fmt_money(p.est_cost_usd, ccy) if p.est_cost_usd else "Free"
        )
        why = "; ".join(p.match_reasons[:2]) if p.match_reasons else ""
        tiles.append(
            f"""
            <div class="wa-tile">
              <div class="wa-tile-head">
                <div>
                  <div class="wa-name">{_esc(p.name)}</div>
                  <div class="wa-where">{_esc(p.city)} · {_esc(p.category)}</div>
                </div>
                <div class="wa-price">{_esc(cost)}</div>
              </div>
              <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <span class="wa-chip gray">⏱ {p.est_visit_minutes} min</span>
                {_conf_chip(p.confidence)}
              </div>
              {f'<div class="wa-why">{_esc(why)}</div>' if why else ""}
            </div>
            """
        )
    st.markdown(f'<div class="wa-grid">{"".join(tiles)}</div>', unsafe_allow_html=True)


def _render_accommodation(plan, ccy: str) -> None:
    total = _fmt_money(plan.total_usd, ccy)
    st.markdown(
        f'<div class="wa-section-title"><span class="wa-dot"></span>'
        f"Stays — total ~{_esc(total)}</div>",
        unsafe_allow_html=True,
    )
    tiles: list[str] = []
    for s in plan.stays:
        per_night = _fmt_money(s.price_per_night_usd, ccy)
        leg_total = _fmt_money(s.total_usd, ccy)
        why = "; ".join(s.match_reasons[:2]) if s.match_reasons else ""
        tiles.append(
            f"""
            <div class="wa-tile">
              <div class="wa-tile-head">
                <div>
                  <div class="wa-name">🏨 {_esc(s.name)}</div>
                  <div class="wa-where">{_esc(s.neighborhood)}, {_esc(s.city)}</div>
                </div>
                <div class="wa-price">{_esc(per_night)}<br>
                  <span style="font-weight:500;color:#6b7280;font-size:.78rem;">/ night</span>
                </div>
              </div>
              <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <span class="wa-chip">{_esc(s.property_type)}</span>
                <span class="wa-chip blue">{s.nights} night{"s" if s.nights != 1 else ""}</span>
                <span class="wa-chip green">Total {_esc(leg_total)}</span>
              </div>
              {f'<div class="wa-why">{_esc(why)}</div>' if why else ""}
            </div>
            """
        )
    st.markdown(f'<div class="wa-grid">{"".join(tiles)}</div>', unsafe_allow_html=True)


def _render_transport(plan, ccy: str) -> None:
    total = _fmt_money(plan.total_usd, ccy)
    st.markdown(
        f'<div class="wa-section-title"><span class="wa-dot"></span>'
        f"Transport — total ~{_esc(total)}</div>",
        unsafe_allow_html=True,
    )
    tiles: list[str] = []
    for leg in plan.legs:
        hh, mm = divmod(leg.duration_minutes, 60)
        cost = _fmt_money(leg.cost_usd, ccy)
        tiles.append(
            f"""
            <div class="wa-tile">
              <div class="wa-tile-head">
                <div>
                  <div class="wa-name">🚄 {_esc(leg.origin)} → {_esc(leg.destination)}</div>
                  <div class="wa-where">{_esc(leg.mode)} · {hh}h {mm:02d}m</div>
                </div>
                <div class="wa-price">{_esc(cost)}</div>
              </div>
              <div style="display:flex;gap:6px;flex-wrap:wrap;">
                {_conf_chip(leg.confidence)}
              </div>
              {f'<div class="wa-why">{_esc(leg.notes)}</div>' if leg.notes else ""}
            </div>
            """
        )
    st.markdown(f'<div class="wa-grid">{"".join(tiles)}</div>', unsafe_allow_html=True)


def _render_budget(budget_obj, ccy: str) -> None:
    total = _fmt_money(budget_obj.total_estimate_usd, ccy)
    title = f"Budget — {total}"
    if budget_obj.budget_amount_usd is not None:
        cap = _fmt_money(budget_obj.budget_amount_usd, ccy)
        title += f" / {cap} cap"
    st.markdown(
        f'<div class="wa-section-title"><span class="wa-dot"></span>'
        f"{_esc(title)}</div>",
        unsafe_allow_html=True,
    )

    if budget_obj.budget_amount_usd:
        ratio = min(
            budget_obj.total_estimate_usd / budget_obj.budget_amount_usd, 1.5
        )
        pct = int(min(ratio, 1.0) * 100)
        over = ratio > 1.0
        bar_cls = "wa-bar-fill over" if over else "wa-bar-fill"
        meta_left = (
            f"{int(ratio * 100)}% of cap"
            if not over else f"{int(ratio * 100)}% — over budget"
        )
        st.markdown(
            f"""
            <div class="wa-card">
              <div class="wa-bar-track">
                <div class="{bar_cls}" style="width:{pct}%"></div>
              </div>
              <div class="wa-bar-meta">
                <span>{_esc(meta_left)}</span>
                <span>Spent {_esc(total)} of {_esc(_fmt_money(budget_obj.budget_amount_usd, ccy))}</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    rows = [
        {
            "Category": line.category,
            f"Estimate ({ccy})": _fmt_money(line.estimate_usd, ccy),
            "Confidence": line.confidence,
            "Notes": line.notes or "",
        }
        for line in budget_obj.lines
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    for w in budget_obj.warnings:
        st.warning(f"⚠️ {w}")


def _render_itinerary(itin, ccy: str) -> None:
    st.markdown(
        '<div class="wa-section-title"><span class="wa-dot"></span>'
        f"Day-by-day itinerary <span class='wa-chip gray' style='margin-left:8px;'>"
        f"confidence: {_esc(itin.confidence)}</span></div>",
        unsafe_allow_html=True,
    )
    if itin.summary:
        st.markdown(
            f'<div class="wa-card"><div class="wa-sub">{_esc(itin.summary)}</div></div>',
            unsafe_allow_html=True,
        )

    for day in itin.days:
        transit_html = ""
        if day.transport_leg:
            t = day.transport_leg
            hh, mm = divmod(t.duration_minutes, 60)
            cost = _fmt_money(t.cost_usd, ccy)
            note = f" — {_esc(t.notes)}" if t.notes else ""
            transit_html = (
                f'<div class="wa-transit">🚄 <strong>Transit:</strong> '
                f"{_esc(t.origin)} → {_esc(t.destination)} via {_esc(t.mode)} "
                f"({hh}h {mm:02d}m, ~{_esc(cost)}){note}</div>"
            )

        acts_html = ""
        for act in day.activities:
            tstr = act.start_time.strftime("%H:%M") if act.start_time else "—"
            cost = (
                f" · ~{_fmt_money(act.est_cost_usd, ccy)}"
                if act.est_cost_usd else ""
            )
            why = (
                f' · <em>{_esc(", ".join(act.match_reasons))}</em>'
                if act.match_reasons else ""
            )
            note = f"<br><span class='wa-meta'>{_esc(act.notes)}</span>" if act.notes else ""
            acts_html += (
                f'<div class="wa-act">'
                f'<span class="wa-time">{_esc(tstr)}</span>'
                f'<span><span class="wa-title">{_esc(act.title)}</span> '
                f'<span class="wa-meta">({act.duration_minutes}m{cost}){why}</span>'
                f"{note}</span></div>"
            )

        day_notes = (
            f'<div class="wa-sub" style="margin-top:8px;">📝 {_esc(day.notes)}</div>'
            if day.notes else ""
        )

        st.markdown(
            f"""
            <div class="wa-day">
              <div class="wa-day-head">
                <span class="wa-day-badge">DAY {day.day_number}</span>
                <span class="wa-day-city">📍 {_esc(day.city)}</span>
                <span class="wa-day-meta">· {day.total_activity_minutes} min planned</span>
              </div>
              {transit_html}
              {acts_html}
              {day_notes}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_critic(verdicts) -> None:
    if not verdicts:
        return
    final = verdicts[-1]
    if final.passed:
        st.markdown(
            f'<div class="wa-verdict pass">✅ Critic approved — '
            f"all rules passed (revision {final.revision}).</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="wa-verdict fail">⚠️ Critic flagged '
            f"{len(final.violations)} issue(s) at revision {final.revision}. "
            f"Plan below is the best-effort output.</div>",
            unsafe_allow_html=True,
        )
    if final.violations:
        rows = [
            {
                "Rule": v.rule,
                "Severity": v.severity,
                "Agent": v.agent,
                "Message": v.message,
            }
            for v in final.violations
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    if len(verdicts) > 1:
        st.caption(
            f"Critic ran {len(verdicts)} time(s); "
            f"{sum(1 for v in verdicts if v.passed)} passing"
        )


def _render_summary(context: TripContext) -> None:
    st.divider()
    totals = context.tracer.totals
    st.markdown(
        '<div class="wa-section-title"><span class="wa-dot"></span>'
        "Run details</div>",
        unsafe_allow_html=True,
    )
    cols = st.columns(4)
    cols[0].metric("Trip ID", context.trip_id)
    cols[1].metric(
        "Tokens", f"{totals['input_tokens']} in / {totals['output_tokens']} out"
    )
    cols[2].metric("Cost (est.)", f"${totals['cost_usd']:.6f}")
    cols[3].metric("Trace file", context.tracer.path.name)
    with st.expander("Decisions log", expanded=False):
        for d in context.decisions:
            st.text(f"• {d}")


def _render_results(context: TripContext, ccy: str) -> None:
    """Render the full trip plan inside MakeMyTrip-style tabs."""
    if context.brief:
        _render_brief(context.brief)

    tabs_spec = [
        ("📅 Itinerary", lambda: context.itinerary and _render_itinerary(context.itinerary, ccy)),
        ("🏨 Stays", lambda: context.accommodation_plan and _render_accommodation(context.accommodation_plan, ccy)),
        ("🚄 Transport", lambda: context.transport_plan and _render_transport(context.transport_plan, ccy)),
        ("💰 Budget", lambda: context.budget and _render_budget(context.budget, ccy)),
        ("🗺️ Things to do", lambda: context.destination_catalog and _render_destinations(context.destination_catalog, ccy)),
        ("🔍 Critic", lambda: _render_critic(context.critic_verdicts)),
    ]
    labels = [label for label, _ in tabs_spec]
    tabs = st.tabs(labels)
    for tab, (_label, fn) in zip(tabs, tabs_spec):
        with tab:
            fn()


# ---------- main ----------


plan_request: str | None = None
if submitted and user_request.strip():
    plan_request = user_request.strip()
    st.session_state.pending_questions = None
    st.session_state.pending_request = None

# If a previous run surfaced open questions, render them with an answer textbox
# so the user can fill in the gaps without rewriting the original request.
if st.session_state.pending_questions and not plan_request:
    questions_html = "".join(
        f"<li>{_esc(q)}</li>" for q in st.session_state.pending_questions
    )
    st.markdown(
        f"""
        <div class="wa-questions">
          <h4>🛑 Just a few details before we plan…</h4>
          <ul>{questions_html}</ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("answer_form"):
        answers = st.text_area(
            "Your answers",
            placeholder="Answer the questions above in plain English…",
            height=140,
            help="Free-form: any order, plain English. We'll merge this with your original request.",
        )
        answer_submitted = st.form_submit_button("✨  Submit answers", type="primary")
    if answer_submitted and answers.strip():
        plan_request = (
            f"{st.session_state.pending_request}\n\n"
            f"Additional details: {answers.strip()}"
        )
        st.session_state.pending_questions = None
        st.session_state.pending_request = None

if plan_request:
    if not os.getenv("GEMINI_API_KEY"):
        st.error("GEMINI_API_KEY is not set. Edit `.env` and refresh.")
        st.stop()

    st.session_state.quota_exhausted_during_revision = False
    started = time.perf_counter()
    try:
        context = _run_pipeline(plan_request)
    except QuotaExhaustedError as e:
        st.error(
            "🚫 **Gemini daily quota exhausted.** "
            "The free tier allows only 20 requests/day for `gemini-2.5-flash`, "
            "and resets ~24h after your first request of the day. "
            "Try again tomorrow, or set `GEMINI_API_KEY` to a paid-tier key in `.env`."
        )
        st.caption(f"Details: {e}")
        st.stop()
    except Exception as e:
        st.error(f"Planning failed: {e}")
        st.stop()
    elapsed_s = time.perf_counter() - started

    if st.session_state.quota_exhausted_during_revision:
        st.warning(
            "⚠️ Gemini daily quota was exhausted during the critic revision step. "
            "The plan below is the pre-revision draft — it may still contain the "
            "violation(s) the critic flagged. Re-run tomorrow (or with a paid-tier "
            "key) to get a revised plan."
        )

    # Pipeline halted at Intent because details are missing — stash the
    # questions in session state and rerun so the answer form renders below.
    if context.brief and context.brief.open_questions:
        st.session_state.pending_questions = list(context.brief.open_questions)
        st.session_state.pending_request = plan_request
        st.rerun()

    st.success(f"🎉 Plan ready in {elapsed_s:.1f}s — explore the tabs below.")

    ccy = _display_ccy(context.brief)
    _render_results(context, ccy)
    _render_summary(context)
