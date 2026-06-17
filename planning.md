# FitFindr — planning.md

> Complete this document before writing any implementation code.
> Your spec and agent diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Your planning.md will be reviewed as part of your submission.
> Update it before starting any stretch features.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

### Tool 1: search_listings

**What it does:**
Searches the mock listings dataset (`data/listings.json`) for secondhand items matching a text description, optionally filtered by size and price ceiling. Scores each candidate by keyword overlap with the description and returns the best matches, most relevant first.

**Input parameters:**
- `description` (str): keywords describing what the user wants, e.g. `"vintage graphic tee"`. Required.
- `size` (str | None): size to filter by, case-insensitive substring match (e.g. `"M"` matches `"S/M"`). `None` skips the size filter.
- `max_price` (float | None): inclusive maximum price. `None` skips the price filter.

**What it returns:**
A `list[dict]` of matching listings, sorted by relevance score (highest first). Each listing dict contains: `id`, `title`, `description`, `category`, `style_tags` (list), `size`, `condition`, `price` (float), `colors` (list), `brand`, `platform`. Returns an empty list `[]` when nothing matches — it does **not** raise.

**What happens if it fails or returns nothing:**
Returns `[]`. The agent (not the tool) detects the empty list, surfaces a helpful message, and — as a fallback — retries with loosened constraints (drops `size`, then `max_price`) before giving up. See Error Handling.

---

### Tool 2: suggest_outfit

**What it does:**
Given a found item and the user's wardrobe, asks the LLM to assemble one or two complete head-to-toe outfit combinations that pair the new item with named pieces the user already owns.

**Input parameters:**
- `new_item` (dict): a listing dict (the item the user is considering buying).
- `wardrobe` (dict): a wardrobe dict with an `items` key holding a list of wardrobe-item dicts (each has `name`, `category`, `colors`, `style_tags`, etc.). May be empty.

**What it returns:**
A non-empty `str` describing 1–2 outfit combinations. If the wardrobe is empty or minimal, it returns general styling advice for the item (what kinds of pieces and vibe pair well) instead of failing.

**What happens if it fails or returns nothing:**
If the wardrobe is empty, it falls back to general styling guidance rather than erroring. If the LLM call fails, it returns a clear error string the agent can show the user — it does not raise or return `""`.

---

### Tool 3: create_fit_card

**What it does:**
Generates a short, shareable, Instagram-caption-style description of the complete outfit and the thrifted find. Uses a higher LLM temperature so output varies for different inputs.

**Input parameters:**
- `outfit` (str): the outfit suggestion string returned by `suggest_outfit()`.
- `new_item` (dict): the listing dict for the thrifted item (used to mention name, price, platform).

**What it returns:**
A 2–4 sentence `str` usable as an OOTD caption — casual and authentic, mentioning the item name, price, and platform once each, and capturing the outfit's vibe in specific terms.

**What happens if it fails or returns nothing:**
If `outfit` is empty or whitespace-only, it returns a descriptive error string (not an exception, not `""`). If the LLM call fails, it returns an error string the agent can surface.

---

### Additional Tools (if any)

None for the base submission. Candidates for stretch: `compare_price(item)` (price-fairness tool) and `load_style_profile()` / `save_style_profile()` (cross-session memory). These will be specced here before implementation.

---

## Planning Loop

**How does your agent decide which tool to call next?**

The loop is a state-driven sequence. After each tool returns, the agent inspects the `session` dict and branches on **what came back** — it does not call all tools unconditionally. Below is the exact conditional logic, specific enough to implement directly.

```
1. session = _new_session(query, wardrobe)

2. PARSE
   parsed = parse_query(query)        # extract description, size, max_price
   session["parsed"] = parsed
   IF parsed["description"] is empty/blank:
       session["error"] = "I couldn't tell what you're looking for —
                           try naming an item, e.g. 'vintage denim jacket'."
       RETURN session                 # early exit, no tools called

3. SEARCH (with retry/fallback)
   results = search_listings(parsed["description"], parsed["size"], parsed["max_price"])
   loosened_notes = []

   IF results == []  AND  parsed["size"] is not None:
       results = search_listings(parsed["description"], None, parsed["max_price"])
       loosened_notes.append("ignored the size filter")

   IF results == []  AND  parsed["max_price"] is not None:
       results = search_listings(parsed["description"], None, None)
       loosened_notes.append("ignored the price limit")

   IF results == []:                  # still nothing after loosening
       session["error"] = "No listings matched 'DESCRIPTION'. Try broader
                           keywords or a higher price."
       RETURN session                 # EARLY EXIT — do NOT call suggest_outfit
   session["search_results"] = results
   IF loosened_notes:                 # tell the user what was relaxed
       session["loosened"] = loosened_notes

4. SELECT ITEM
   session["selected_item"] = results[0]      # top-ranked match

5. SUGGEST OUTFIT
   outfit = suggest_outfit(session["selected_item"], session["wardrobe"])
   IF outfit is empty or starts with the tool's error sentinel:
       session["error"] = "I found ITEM but couldn't build an outfit right now."
       RETURN session                 # early exit, skip fit card
   session["outfit_suggestion"] = outfit
   # note: suggest_outfit itself handles the empty-wardrobe case internally
   #       by returning general styling advice, so the loop does not branch on that.

6. CREATE FIT CARD
   card = create_fit_card(session["outfit_suggestion"], session["selected_item"])
   IF card starts with the tool's error sentinel:
       session["error"] = "Outfit's ready, but I couldn't write a caption."
       RETURN session
   session["fit_card"] = card

7. RETURN session                     # success: error is None, fit_card is set
```

**Termination:** the loop ends when `session["fit_card"]` is set (success) or as soon as any step sets `session["error"]` and returns early (failure). Every branch either advances to the next tool or terminates — there is no path that calls a downstream tool with empty/invalid input.

---

## State Management

**How does information from one tool get passed to the next?**

A single `session` dict (created by `_new_session()` in `agent.py`) is the single source of truth for one interaction. Each tool reads from and writes to it:

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | entry point | parse step |
| `parsed` (description/size/max_price) | parse step | `search_listings` |
| `search_results` | `search_listings` | item-selection step |
| `selected_item` | item-selection step | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | entry point | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | final output |
| `error` | any step that aborts | final output |

Because the item found by `search_listings` is stored in `selected_item`, it flows directly into `suggest_outfit` and `create_fit_card` — the user never re-enters it. State lives in memory for the duration of one `run_agent()` call.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response (what the user actually sees / is offered) |
|------|-------------|-----------|
| search_listings | No results match the query | Tool returns `[]`. Agent retries automatically — first without the size filter, then without the price cap — and on success says e.g. *"No size M vintage tees came up, so I searched all sizes and found these."* If still empty after loosening, the agent says *"I couldn't find any 'vintage graphic tee' listings, even after relaxing your size and price. Want to try broader keywords (e.g. 'graphic tee') or raise your budget?"* — it names what it tried and offers two concrete next steps rather than a bare "no results." |
| suggest_outfit | Wardrobe is empty / minimal | Instead of refusing, the tool returns **general styling advice** for the item: *"Your wardrobe's empty, so here's how I'd style this graphic tee from scratch — pair it with high-waisted denim and white sneakers for an easy everyday fit, or a pleated midi skirt to dress it up."* If the LLM call itself errors, the agent says *"I found the item but couldn't generate styling ideas right now — try again in a moment."* (never a blank or silent failure). |
| create_fit_card | Outfit input missing/incomplete | If `outfit` is empty/whitespace, the tool returns a message the agent shows verbatim: *"I don't have an outfit to write a caption for yet — let's pick out a look first."* If the LLM call fails, the agent says *"Your outfit's ready, but I couldn't write a caption — here's the outfit on its own:"* and still shows the `outfit_suggestion`, so the user keeps the useful part. |

Strategy summary: tools never crash the agent and never fail silently — they return either valid data or an explicit signal (`[]` / error string). The agent inspects those signals and chooses to retry, fall back, or report to the user.

---

## Architecture

```
 User query
     │
     │ "vintage graphic tee under $30, size M"
     ▼
 Planning Loop ─────────────────────────────────────────────────────────────────┐
     │                                                                            │
     │  parse_query(query) → {description, size, max_price}                       │
     │  writes ▸ Session.parsed                                                   │
     │       │ description blank                                                  │
     │       ├──► [ERROR] "Couldn't tell what you're looking for" ──────────────► │
     │       │                                                                    │
     │       ▼ description ok                                                     │
     ├─► search_listings(description, size, max_price)                            │
     │       │ results=[]  → retry: drop size, then drop max_price                │
     │       │              (writes ▸ Session.loosened = ["ignored size", ...])   │
     │       │ results=[] after loosening                                         │
     │       ├──► [ERROR] "No listings matched 'DESCRIPTION'..." ───────────────► │
     │       │                                                                    │
     │       │ results=[item, ...]   writes ▸ Session.search_results              │
     │       ▼                                                                    │
     │   Session.selected_item = results[0]                                       │
     │       │ selected_item ──────────────┐ wardrobe                             │
     ├─► suggest_outfit(selected_item, wardrobe)                                  │
     │       │ empty wardrobe → general styling advice (internal fallback)        │
     │       │ tool error sentinel                                                │
     │       ├──► [ERROR] "Found ITEM but couldn't build an outfit" ────────────► │
     │       │                                                                    │
     │       │ outfit string   writes ▸ Session.outfit_suggestion                 │
     │       ▼                                                                    │
     ├─► create_fit_card(outfit_suggestion, selected_item)                        │
     │       │ tool error sentinel                                                │
     │       ├──► [ERROR] "Couldn't write a caption" ───────────────────────────► │
     │       │                                                                    │
     │       │ caption string   writes ▸ Session.fit_card                         │
     │       ▼                                                                    │
     │   Return session  (error=None, fit_card set)                              │
     │                                                                            │
     └──────────────────────────── all [ERROR] paths set Session.error ◄─────────┘
                                    and return session early (loop stops)

 ┌──────────────────────────────── Session state (single source of truth) ───────┐
 │ query · parsed · search_results · loosened · selected_item · wardrobe ·        │
 │ outfit_suggestion · fit_card · error                                           │
 │ ← every tool reads its inputs from here and writes its result back here →      │
 └────────────────────────────────────────────────────────────────────────────┘
```

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**

I'll use **Claude (via Claude Code)** for all three tools, implementing one at a time.

- **`search_listings`** — Input I'll give Claude: the *Tool 1* block above (the three parameters, the `list[dict]` return shape with field names, and the "returns `[]`, never raises" failure mode), plus the `load_listings()` signature and field list from `utils/data_loader.py`. Expected output: pure-Python (no LLM) code that filters by `max_price` and `size`, scores remaining listings by keyword overlap with `description`, drops zero-score items, and sorts descending. Verification before trusting it: read the code to confirm it (a) applies all three filters, (b) returns `[]` rather than raising on no match, then run 3 queries — a normal match ("graphic tee"), an impossible filter ("ballgown size XXS under $5" → expect `[]`), and a loosened re-run.
- **`suggest_outfit`** — Input: the *Tool 2* block + the wardrobe item schema. Expected output: a Groq LLM call that branches on `wardrobe["items"]` being empty (general advice) vs. populated (specific combinations naming owned pieces), returning a non-empty string. Verification: run once with `get_example_wardrobe()` and once with `get_empty_wardrobe()`, confirming the empty case still returns useful advice and never `""`.
- **`create_fit_card`** — Input: the *Tool 3* block (style guidelines, the empty-`outfit` guard, "higher temperature"). Expected output: a Groq call returning a 2–4 sentence caption mentioning item/price/platform. Verification: call it twice on the same item to confirm the captions differ, and once with `outfit=""` to confirm it returns the error string instead of raising.

**Milestone 4 — Planning loop and state management:**

- AI tool: **Claude (Claude Code)**. Input I'll give it: the **Planning Loop** pseudocode block, the **State Management** table, and the **Architecture** diagram from this file, plus the `agent.py` stub (`_new_session()` and the `run_agent()` TODO).
- Expected output: a `run_agent()` that follows the pseudocode branch-for-branch — parse → search (with the size/price loosening retries) → select `results[0]` → suggest → fit card — reading inputs from and writing results to the `session` dict, and returning early with `session["error"]` set at each failure branch.
- Verification: run the two CLI scenarios in `agent.py` (happy path + `designer ballgown size XXS under $5` no-results path). Confirm the found item flows into `suggest_outfit`/`create_fit_card` with no re-entry, the no-results path sets `session["error"]` and never calls the downstream tools, and a loosened search records `session["loosened"]`.

---

## A Complete Interaction (Step by Step)

Write out what a full user interaction looks like from start to finish — tool call by tool call. Use a specific example query.

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 0 — init & parse:** `run_agent()` builds the session, then `parse_query()` extracts `description="vintage graphic tee"`, `size=None` (the user didn't give one), `max_price=30.0`. These are stored in `session["parsed"]`. `description` is non-blank, so the loop proceeds.

**Step 1 — search_listings:** Called with `search_listings("vintage graphic tee", None, 30.0)`.
- *Input:* description + no size filter + $30 ceiling.
- *Returns:* a non-empty `list[dict]` of tees scored by keyword overlap, highest first — e.g. top result `{"title": "Vintage Band Graphic Tee", "price": 24.0, "platform": "depop", "condition": "good", ...}`.
- The loop stores the list in `session["search_results"]`. Because results are non-empty, no loosening is needed.

**Step 2 — select item:** The loop sets `session["selected_item"] = results[0]` (the $24 Depop band tee). This is the state that flows into the next two tools — the user never re-types it.

**Step 3 — suggest_outfit:** Called with `suggest_outfit(selected_item, wardrobe)`, where the wardrobe (passed in at entry) already contains baggy jeans and chunky sneakers.
- *Input:* the selected tee dict + the populated wardrobe.
- *Returns:* a non-empty string, e.g. *"Pair the band tee with your baggy jeans and chunky sneakers for an off-duty skater look; layer a flannel over it when it's cooler."*
- Stored in `session["outfit_suggestion"]`. (Wardrobe is non-empty, so no general-advice fallback.)

**Step 4 — create_fit_card:** Called with `create_fit_card(outfit_suggestion, selected_item)`.
- *Input:* the outfit string + the tee dict (for name/price/platform).
- *Returns:* a 2–4 sentence caption, e.g. *"Thrifted dreams do come true 🌀 Snagged this vintage band tee for $24 on Depop and threw it on with baggy jeans + chunky sneakers — peak off-duty energy."*
- Stored in `session["fit_card"]`. The loop returns the session with `error=None`.

**Final output to user:**
The user sees the found item (title, $24, Depop, condition), the suggested outfit, and the shareable fit-card caption above. **Contrast (no-results branch):** had the query been "designer ballgown size XXS under $5", `search_listings` would return `[]`, the loop would retry without size then without price, still get `[]`, set `session["error"]`, and the user would see *"I couldn't find any 'designer ballgown' listings, even after relaxing your size and price — want to try broader keywords or raise your budget?"* — and `suggest_outfit`/`create_fit_card` would never be called.
