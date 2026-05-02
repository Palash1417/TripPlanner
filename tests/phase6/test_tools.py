"""Phase 6 unit tests — one per tool. All offline (no network)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from src.tools import currency, geo, place_details, transport_lookup, web_search


# ---------- 6a: geo ----------


def test_haversine_zero_distance() -> None:
    assert geo.haversine_km(35.0, 139.0, 35.0, 139.0) == pytest.approx(0.0, abs=1e-6)


def test_haversine_tokyo_to_kyoto_known_distance() -> None:
    # Tokyo Station ~ (35.681, 139.767), Kyoto Station ~ (34.985, 135.758)
    # Real great-circle distance is ~365 km.
    d = geo.haversine_km(35.681, 139.767, 34.985, 135.758)
    assert 350 <= d <= 380


def test_haversine_antipodal_is_half_earth() -> None:
    # Two antipodal points should be ~20015 km (half Earth's circumference).
    d = geo.haversine_km(0.0, 0.0, 0.0, 180.0)
    assert 19_900 <= d <= 20_100


def test_distance_between_returns_none_when_missing_coords() -> None:
    assert geo.distance_between(None, (1.0, 2.0)) is None
    assert geo.distance_between((1.0, 2.0), None) is None
    assert geo.distance_between((None, 2.0), (1.0, 2.0)) is None


# ---------- 6b: currency ----------


def test_currency_to_usd_identity() -> None:
    assert currency.to_usd(100.0, "USD") == 100.0


def test_currency_to_usd_known_currency() -> None:
    # JPY rate is approx 0.0067 — 100,000 JPY ~= $670 USD
    usd = currency.to_usd(100_000, "JPY")
    assert 600 <= usd <= 750


def test_currency_convert_round_trip() -> None:
    eur = currency.convert(1000.0, "USD", "EUR")
    back = currency.convert(eur, "EUR", "USD")
    assert back == pytest.approx(1000.0, rel=1e-6)


def test_currency_convert_same_returns_input() -> None:
    assert currency.convert(42.0, "USD", "USD") == 42.0


def test_currency_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown currency"):
        currency.to_usd(10, "XXX")


def test_currency_supported_includes_majors() -> None:
    sup = currency.supported()
    for code in ("USD", "EUR", "GBP", "JPY", "INR"):
        assert code in sup


# ---------- 6e: transport_lookup ----------


def test_transport_lookup_known_route() -> None:
    info = transport_lookup.lookup("Tokyo", "Kyoto")
    assert info is not None
    assert info.mode == "train"
    assert info.duration_minutes == 135
    assert "Shinkansen" in info.notes
    assert info.cost_usd_min < info.cost_usd_max
    assert info.cost_usd_min <= info.cost_usd_mid <= info.cost_usd_max


def test_transport_lookup_is_bidirectional() -> None:
    a = transport_lookup.lookup("Kyoto", "Tokyo")
    b = transport_lookup.lookup("Tokyo", "Kyoto")
    assert a == b


def test_transport_lookup_case_insensitive() -> None:
    a = transport_lookup.lookup("tokyo", "KYOTO")
    b = transport_lookup.lookup("Tokyo", "Kyoto")
    assert a == b


def test_transport_lookup_unknown_returns_none() -> None:
    assert transport_lookup.lookup("Atlantis", "Springfield") is None


def test_transport_lookup_known_pairs_nonempty() -> None:
    pairs = transport_lookup.known_pairs()
    assert len(pairs) >= 5


# ---------- 6c: web_search ----------


def test_web_search_no_provider_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("TAVILY_API_KEY", "SERPER_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert web_search.search("anything") == []
    assert web_search.is_available() is False


def test_web_search_empty_query_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "fake")
    assert web_search.search("   ") == []


def test_web_search_tavily_normalizes_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "fake")
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "results": [
            {"title": "T1", "url": "u1", "content": "snip1"},
            {"title": "T2", "url": "u2", "content": "snip2"},
        ]
    }
    fake_response.raise_for_status.return_value = None
    monkeypatch.setattr(web_search.httpx, "post", lambda *a, **k: fake_response)

    results = web_search.search("kyoto temples", max_results=5)
    assert results == [
        {"title": "T1", "url": "u1", "snippet": "snip1"},
        {"title": "T2", "url": "u2", "snippet": "snip2"},
    ]


def test_web_search_swallows_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge case 8.3: tool failures must not crash the planning run."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake")

    def boom(*a, **k):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(web_search.httpx, "post", boom)
    assert web_search.search("anything") == []


def test_web_search_provider_selection_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tavily takes priority over Serper, Serper over Brave."""
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.setenv("SERPER_API_KEY", "s")
    monkeypatch.setenv("BRAVE_API_KEY", "b")
    called = {"name": None}

    def fake_tavily(query, max_results):
        called["name"] = "tavily"
        return [{"title": "x", "url": "y", "snippet": "z"}]

    monkeypatch.setattr(web_search, "_tavily", fake_tavily)
    web_search.search("anything")
    assert called["name"] == "tavily"


# ---------- 6d: place_details ----------


def test_place_details_unavailable_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    assert place_details.is_available() is False
    assert place_details.lookup("Senso-ji", "Tokyo") is None


def test_place_details_stub_returns_none_even_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 is a stub; v2 will hit the real Maps API."""
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fake")
    assert place_details.is_available() is True
    assert place_details.lookup("Senso-ji", "Tokyo") is None
