"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card

# Tools that can fail "softly" (suggest_outfit, create_fit_card) signal an error
# by returning a string of the form "[<tool> error] ..." instead of raising. The
# planning loop checks for it so a failed tool never silently poisons the output.
_TOOL_ERROR_RE = re.compile(r"^\s*\[\w+ error\]", re.IGNORECASE)


# ── query parsing ─────────────────────────────────────────────────────────────

# Bare size tokens we trust WITHOUT an explicit "size" keyword: only ones that
# are unambiguous as sizes (contain a slash or an X). Single letters S/M/L are
# deliberately excluded here — they collide with ordinary words (the "s" in
# "what's", the "l" in a brand) — so they only count after the word "size".
_BARE_SIZES = ["S/M", "M/L", "L/XL", "XXL", "XS", "XL"]

# Phrases that mark the boundary of the searchable description. Anything from
# the first of these onward is a filter or conversational aside, not a keyword.
_DESC_CUTOFF = re.compile(
    r"[.?!]|\b(?:under|below|less than|max|in size|size[:\s]|for under|that'?s|i\b)",
    re.IGNORECASE,
)


def parse_query(query: str) -> dict:
    """
    Extract a search description, size, and max_price from a natural-language query.

    Uses lightweight regex/string parsing (no LLM) so it is fast and deterministic:
        - max_price:   the first "$N", "under N", "below N", etc.
        - size:        "size M" / "size: 8" (explicit keyword), or an unambiguous
                       bare token like "S/M" or "XL". Bare single letters are NOT
                       treated as sizes — they require the word "size".
        - description: only the leading noun phrase. Everything from the first
                       sentence break or filter phrase onward is dropped so
                       trailing asides don't pollute keyword matching.

    Returns a dict: {"description": str, "size": str | None, "max_price": float | None}.
    """
    text = query.strip()
    lowered = text.lower()

    # max_price ── "$30", "under 30", "below 30", "less than 30"
    max_price = None
    price_match = re.search(
        r"(?:under|below|less than|max|<=?)\s*\$?\s*(\d+(?:\.\d+)?)|\$\s*(\d+(?:\.\d+)?)",
        lowered,
    )
    if price_match:
        raw = price_match.group(1) or price_match.group(2)
        max_price = float(raw)

    # size ── explicit "size X" wins; otherwise only an unambiguous bare token.
    size = None
    size_match = re.search(r"\bsize[:\s]+([a-z0-9/.]+)", lowered)
    if size_match:
        size = size_match.group(1).upper()
    else:
        for candidate in _BARE_SIZES:
            if re.search(rf"\b{re.escape(candidate.lower())}\b", lowered):
                size = candidate
                break

    # description ── strip leading filler FIRST (so "I'm looking for" doesn't get
    # mistaken for the "I ..." aside cutoff), then keep only the leading phrase
    # before the first sentence break or filter/aside phrase.
    stripped = re.sub(
        r"^\s*(?:i'?m\s+|i\s+am\s+|looking for\s+|searching for\s+|want\s+|need\s+|"
        r"a\s+|an\s+|the\s+|some\s+)+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cutoff = _DESC_CUTOFF.search(stripped)
    description = stripped[: cutoff.start()] if cutoff else stripped
    description = re.sub(r"\b(a|an|the|some)\b", " ", description, flags=re.IGNORECASE)
    description = re.sub(r"\s+", " ", description).strip(" .,-")

    # Fallback: if cutting left nothing (e.g. the query led with a filter word),
    # fall back to the original text minus the price phrase so we still search.
    if not description:
        description = text
        if price_match:
            description = description.replace(price_match.group(0), " ")
        description = re.sub(r"\s+", " ", description).strip(" .,-")

    return {"description": description, "size": size, "max_price": max_price}


def _is_tool_error(text) -> bool:
    """A soft-failing tool returned nothing usable, or signaled an error."""
    return not text or not text.strip() or bool(_TOOL_ERROR_RE.match(text))


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1 — fresh session, the single source of truth for this interaction.
    session = _new_session(query, wardrobe)

    # Step 2 — PARSE the query into description / size / max_price.
    parsed = parse_query(query)
    session["parsed"] = parsed
    if not parsed["description"]:
        session["error"] = (
            "I couldn't tell what you're looking for — try naming an item, "
            "e.g. 'vintage denim jacket under $40'."
        )
        return session  # early exit, no tools called

    # Step 3 — SEARCH, retrying with loosened constraints if nothing matches.
    description = parsed["description"]
    results = search_listings(description, parsed["size"], parsed["max_price"])
    loosened = []

    if not results and parsed["size"] is not None:
        results = search_listings(description, None, parsed["max_price"])
        if results:
            loosened.append(f"ignored the size filter ({parsed['size']})")

    if not results and parsed["max_price"] is not None:
        results = search_listings(description, None, None)
        if results:
            loosened.append(f"ignored the ${parsed['max_price']:.0f} price limit")

    if not results:  # still nothing after loosening — stop before downstream tools
        session["error"] = (
            f"No listings matched '{description}'. Try broader keywords "
            "or a higher price."
        )
        return session

    session["search_results"] = results
    if loosened:
        session["loosened"] = loosened

    # Step 4 — SELECT the top-ranked match to carry forward.
    session["selected_item"] = results[0]
    item_title = session["selected_item"].get("title", "this item")

    # Step 5 — SUGGEST an outfit. (suggest_outfit handles the empty-wardrobe case
    # itself by returning general styling advice, so we don't branch on that here.)
    outfit = suggest_outfit(session["selected_item"], session["wardrobe"])
    if _is_tool_error(outfit):
        session["error"] = (
            f"I found {item_title}, but couldn't build an outfit right now. "
            "Please try again in a moment."
        )
        return session
    session["outfit_suggestion"] = outfit

    # Step 6 — CREATE the shareable fit card.
    card = create_fit_card(session["outfit_suggestion"], session["selected_item"])
    if _is_tool_error(card):
        session["error"] = (
            "Your outfit's ready, but I couldn't write a caption for it right now."
        )
        return session
    session["fit_card"] = card

    # Step 7 — success: error stays None and fit_card is set.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
