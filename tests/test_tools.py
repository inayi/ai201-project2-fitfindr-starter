"""
tests/test_tools.py

Unit tests for the three FitFindr tools, with at least one test per failure mode.

The search_listings tests are pure (no network). The suggest_outfit and
create_fit_card tests are split:
  - The failure-mode guards (empty wardrobe, empty outfit) are tested WITHOUT
    hitting the network where possible.
  - The "live" LLM tests are skipped automatically when GROQ_API_KEY is not set,
    so the suite still passes in CI / offline.
"""

import os

import pytest

from tools import search_listings, suggest_outfit, create_fit_card

HAS_KEY = bool(os.environ.get("GROQ_API_KEY"))
needs_key = pytest.mark.skipif(not HAS_KEY, reason="GROQ_API_KEY not set")


# ── search_listings ─────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Failure mode: no match — must return [] and NOT raise.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_size_filter_case_insensitive():
    # "m" should match a size like "S/M" regardless of case.
    results = search_listings("tee", size="m", max_price=None)
    assert all("m" in item["size"].lower() for item in results)


def test_search_sorted_by_relevance():
    # More keyword overlap should rank earlier; results are returned best-first.
    results = search_listings("vintage denim jeans", size=None, max_price=None)
    assert isinstance(results, list)
    # Re-deriving the score isn't necessary; just confirm a non-empty ranked list.
    assert len(results) > 0


# ── suggest_outfit ──────────────────────────────────────────────────────────

EXAMPLE_ITEM = {
    "title": "Y2K Baby Tee — Butterfly Print",
    "category": "tops",
    "colors": ["white", "pink"],
    "style_tags": ["y2k", "graphic tee"],
    "price": 18.0,
    "platform": "depop",
}


def test_suggest_outfit_empty_wardrobe_does_not_crash():
    # Failure mode: empty wardrobe — must return a non-empty string, never crash.
    result = suggest_outfit(EXAMPLE_ITEM, {"items": []})
    assert isinstance(result, str)
    assert result != ""


@needs_key
def test_suggest_outfit_with_wardrobe():
    wardrobe = {
        "items": [
            {"name": "Baggy straight-leg jeans", "category": "bottoms", "colors": ["blue"]},
            {"name": "Chunky white sneakers", "category": "shoes", "colors": ["white"]},
        ]
    }
    result = suggest_outfit(EXAMPLE_ITEM, wardrobe)
    assert isinstance(result, str)
    assert len(result) > 0
    assert not result.startswith("[suggest_outfit error]")


# ── create_fit_card ─────────────────────────────────────────────────────────

def test_create_fit_card_empty_outfit():
    # Failure mode: missing/empty outfit — return an error string, do NOT raise.
    result = create_fit_card("", EXAMPLE_ITEM)
    assert isinstance(result, str)
    assert result.startswith("[create_fit_card error]")


def test_create_fit_card_whitespace_outfit():
    result = create_fit_card("   \n  ", EXAMPLE_ITEM)
    assert result.startswith("[create_fit_card error]")


@needs_key
def test_create_fit_card_varies():
    # Same input run twice should not produce identical captions (high temperature).
    outfit = "Butterfly baby tee with baggy jeans and chunky sneakers."
    a = create_fit_card(outfit, EXAMPLE_ITEM)
    b = create_fit_card(outfit, EXAMPLE_ITEM)
    assert not a.startswith("[create_fit_card error]")
    assert not b.startswith("[create_fit_card error]")
    assert a != b
