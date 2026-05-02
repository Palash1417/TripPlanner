"""Web search — provider-agnostic wrapper.

Phase 6c deliverable. Picks a provider based on which API key is set:
  TAVILY_API_KEY > SERPER_API_KEY > BRAVE_API_KEY

Free-tier choices:
  - Tavily: 1k free searches/month — https://tavily.com (recommended)
  - Serper: 2.5k free queries — https://serper.dev
  - Brave:  2k free queries/month — https://brave.com/search/api

When no key is set, returns []. Callers (Destination, Accommodation agents)
treat this as "no extra grounding available" and fall back to LLM-only output.
"""

from __future__ import annotations

import os
import sys
from typing import TypedDict

import httpx


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str


_TIMEOUT_SECONDS = 8.0


def search(query: str, *, max_results: int = 4) -> list[SearchResult]:
    """Search the web, return normalized results. Returns [] if no provider configured."""
    if not query.strip():
        return []
    try:
        if os.getenv("TAVILY_API_KEY"):
            return _tavily(query, max_results)
        if os.getenv("SERPER_API_KEY"):
            return _serper(query, max_results)
        if os.getenv("BRAVE_API_KEY"):
            return _brave(query, max_results)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        # Tool failures must not crash the planning run (edge case 8.3).
        print(f"[web_search] {type(e).__name__}: {e}", file=sys.stderr)
        return []
    return []


def is_available() -> bool:
    return any(
        os.getenv(k)
        for k in ("TAVILY_API_KEY", "SERPER_API_KEY", "BRAVE_API_KEY")
    )


# --- providers -------------------------------------------------------------


def _tavily(query: str, max_results: int) -> list[SearchResult]:
    response = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": os.environ["TAVILY_API_KEY"],
            "query": query,
            "max_results": max_results,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])[:max_results]
    ]


def _serper(query: str, max_results: int) -> list[SearchResult]:
    response = httpx.post(
        "https://google.serper.dev/search",
        json={"q": query, "num": max_results},
        headers={
            "X-API-KEY": os.environ["SERPER_API_KEY"],
            "Content-Type": "application/json",
        },
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return [
        {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
        for r in data.get("organic", [])[:max_results]
    ]


def _brave(query: str, max_results: int) -> list[SearchResult]:
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": max_results},
        headers={
            "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
            "Accept": "application/json",
        },
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    web_results = data.get("web", {}).get("results", [])
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", ""),
        }
        for r in web_results[:max_results]
    ]
