# FitFindr 🛍️

FitFindr is a tool-using agent for secondhand fashion. You describe what you're
looking for in plain language; the agent searches a mock marketplace, styles the
best find against your wardrobe, and writes a shareable "fit card" caption for it.

It runs as a Gradio web app and as a CLI.

---

## Setup

```bash
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file in the project root (free key at
[console.groq.com](https://console.groq.com)):

```
GROQ_API_KEY=your_key_here
```

Run it:

```bash
python app.py        # Gradio web UI (opens on http://localhost:7860)
python agent.py      # CLI: runs a happy-path and a no-results scenario
```

## Project layout

```
ai201-project2-fitfindr-starter/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # Wardrobe format + example/empty wardrobes
├── utils/
│   └── data_loader.py         # Loaders for listings + wardrobe
├── tools.py                   # The 3 tools (search / suggest / fit card)
├── agent.py                   # Planning loop, query parsing, session state
├── app.py                     # Gradio interface
├── planning.md                # Spec + architecture diagram
└── README.md
```

---

## Tool Inventory

The agent uses three tools, each a standalone function in [`tools.py`](tools.py)
that can be called and tested in isolation.

### 1. `search_listings(description, size, max_price) → list[dict]`

**Purpose:** Find secondhand listings matching the user's request. Pure Python —
no LLM — so it's fast and deterministic.

| Parameter | Type | Meaning |
|-----------|------|---------|
| `description` | `str` | Keywords describing the item, e.g. `"vintage graphic tee"`. Required. |
| `size` | `str \| None` | Size filter, case-insensitive substring match (`"M"` matches `"S/M"`). `None` skips the filter. |
| `max_price` | `float \| None` | Inclusive price ceiling. `None` skips the filter. |

**Returns:** a `list[dict]` of listings sorted by relevance (keyword-overlap score,
highest first). Each listing has `id`, `title`, `description`, `category`,
`style_tags` (list), `size`, `condition`, `price` (float), `colors` (list),
`brand`, `platform`. Returns `[]` when nothing matches — it never raises.

### 2. `suggest_outfit(new_item, wardrobe) → str`

**Purpose:** Style the found item into 1–2 complete outfits using pieces the user
already owns. LLM-backed (Groq `llama-3.3-70b-versatile`, temperature 0.7).

| Parameter | Type | Meaning |
|-----------|------|---------|
| `new_item` | `dict` | A listing dict (the item under consideration). |
| `wardrobe` | `dict` | A wardrobe dict with an `items` list (each item has `name`, `category`, `colors`, `style_tags`). May be empty. |

**Returns:** a non-empty `str` describing outfit combinations. If the wardrobe is
empty/minimal, it returns general styling advice instead of failing.

### 3. `create_fit_card(outfit, new_item) → str`

**Purpose:** Turn the outfit into a short, shareable OOTD caption. LLM-backed at
temperature 1.0, so the same item produces a different caption each time.

| Parameter | Type | Meaning |
|-----------|------|---------|
| `outfit` | `str` | The outfit string from `suggest_outfit()`. |
| `new_item` | `dict` | The listing dict, used to mention name, price, and platform. |

**Returns:** a 2–4 sentence caption `str`, casual and authentic, naming the item,
its price, and the platform once each.

---

## Planning Loop

The loop lives in `run_agent()` in [`agent.py`](agent.py). It is **state-driven,
not a fixed sequence** — after each tool returns, it inspects the session and
branches on *what came back*. It never calls all three tools unconditionally.

1. **Parse** the query (`parse_query()`) into `description`, `size`, `max_price`
   using lightweight regex/string parsing (no LLM). If no usable description is
   found, it stops immediately and asks the user to name an item.
2. **Search.** Call `search_listings()`. Then branch on the result:
   - **Results found** → keep them, select the top match, continue.
   - **Empty** → enter the **retry/fallback** branch: re-search with `size`
     dropped, then with `max_price` dropped, recording each relaxation.
   - **Still empty** → set `session["error"]` and return *before* calling any
     downstream tool (never style or caption nothing).
3. **Select** the top-ranked listing as `selected_item`.
4. **Suggest outfit** for that item against the wardrobe. (The empty-wardrobe
   case is handled inside the tool, so the loop doesn't branch on it; it only
   branches if the tool reports an outright failure.)
5. **Create fit card** from the outfit — only if a valid outfit string exists.
6. **Done** when `fit_card` is set, or earlier if any step set `error`.

The loop terminates on success (fit card produced) or on the first error
(recorded and returned early). No branch ever passes empty/invalid input forward.

---

## State Management

A single **`session` dict** (built by `_new_session()`) is the source of truth
for one interaction. Each step reads upstream fields and writes its own, so data
found by one tool flows to the next without the user re-entering anything.

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | entry point | parse step |
| `parsed` | parse step | `search_listings` |
| `search_results` | `search_listings` | item selection |
| `selected_item` | item selection | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | entry point | `suggest_outfit` |
| `loosened` | search fallback | UI (shows what was relaxed) |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | final output |
| `error` | any aborting step | final output |

**Verified state passing.** Instrumenting the tools during a run of the example
query confirmed the *same objects* flow through (identity checks, not just
equality):

```
selected_item IS the dict passed into suggest_outfit:  True
selected_item IS the dict passed into create_fit_card: True
outfit_suggestion IS what suggest_outfit returned:     True
outfit_suggestion IS what went into create_fit_card:   True
```

The `lst_002` listing found by `search_listings` was the exact dict styled and
captioned downstream — the user never re-typed it.

---

## Error Handling

Error handling was a **design decision before a coding problem**. For each tool
we asked: *what's least frustrating for the user if this fails?* The rule across
all three: **no tool crashes the agent and no tool fails silently.** Each returns
either valid data or an explicit signal — `[]` (search) or a `"[<tool> error] …"`
string (the LLM tools) — that the loop inspects via `_is_tool_error()`.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No listings match | Returns `[]`. The loop retries with loosened constraints (drop `size`, then `max_price`), tells the user what it relaxed, and only reports "no matches" if everything still returns empty. |
| `suggest_outfit` | Empty/minimal wardrobe | The tool detects an empty `items` list and returns general styling advice instead of erroring, so a brand-new user isn't stuck. |
| `suggest_outfit` | LLM/network/auth failure | Wrapped in `try/except`; returns `"[suggest_outfit error] …"`. The loop detects the prefix, stops, and shows a friendly message rather than a broken outfit. |
| `create_fit_card` | Missing/empty `outfit` input | Guards up front and returns `"[create_fit_card error] …"` — no blank caption, no exception. |
| `create_fit_card` | LLM/network/auth failure | Same `try/except` + error-string pattern; the loop reports "couldn't write a caption" but the outfit is still shown. |

**Concrete example from testing — the no-results path.** Running the deliberate
stress query `"designer ballgown size XXS under $5"`:

- `parse_query` →
  `{'description': 'designer ballgown', 'size': 'XXS', 'max_price': 5.0}`
- `search_listings("designer ballgown", "XXS", 5.0)` → `[]` (nothing that cheap
  in that size). The loop retries dropping size, then price — still `[]`, because
  no listing matches "designer ballgown" at all.
- Result: the loop sets
  `session["error"] = "No listings matched 'designer ballgown'. Try broader keywords or a higher price."`
  and returns **before** `suggest_outfit`/`create_fit_card` are ever called. The
  UI shows that message in the listing panel and leaves the other two blank.

**A second concrete example — the soft-fail detector.** Unit-testing
`_is_tool_error()` confirmed the loop catches the tools' error strings (this also
caught a real bug: the detector originally looked for a `⚠️` prefix while the
tools emit `[tool error]`, so failures would have slipped through — now aligned):

```
_is_tool_error("[create_fit_card error] No outfit was provided.")  → True
_is_tool_error("Pair the tee with baggy jeans!")                   → False
```

---

## AI Usage

I used **Claude (via Claude Code)** to help implement the project, working from
the spec and architecture diagram in [`planning.md`](planning.md). Two specific
instances:

### Instance 1 — implementing the planning loop (`run_agent`)

- **Input I gave it:** the **Planning Loop** pseudocode block and **State
  Management** table from `planning.md`, the **Architecture** ASCII diagram, and
  the `agent.py` stub (the `_new_session` shape and the numbered TODO).
- **What it produced:** a `run_agent()` that initialized the session, parsed the
  query, ran the search with the two-stage loosening fallback, selected the top
  item, and called the two LLM tools with early-exit error checks — matching the
  diagram's branch points.
- **What I changed/overrode:** I had it factor query parsing into a separate
  `parse_query()` and introduce a shared `_is_tool_error()` helper rather than
  inlining the checks. I also **overrode its first parser**: it accepted bare
  single-letter sizes, which misfired — the standalone `s` in `"what's"` parsed
  as `size="S"`, and trailing conversational sentences leaked into the search
  keywords. I rewrote it to require an explicit `size` keyword for single letters
  and to cut the description at the first sentence break / filter phrase, then
  re-tested against all the example queries.

### Instance 2 — implementing the Gradio handler (`handle_query`)

- **Input I gave it:** the **Tool Inventory** and **State Management** sections of
  `planning.md` (specifically the `session` field names and the listing dict
  schema), plus the `app.py` stub with its numbered TODO and the already-wired
  output panels.
- **What it produced:** a `handle_query()` that guards empty queries, maps the
  radio choice to the example/empty wardrobe, calls `run_agent()`, and routes
  `session["error"]` to the first panel or the three result fields on success.
- **What I changed/overrode:** Claude's version ignored `session["loosened"]`, so
  a user whose filters were silently relaxed wouldn't know. I added a "Note: I
  ignored the size filter …" line to the top of the listing panel so the
  fallback is visible — which is the behavior the planning doc's error-handling
  strategy promised.

**Verification before trusting output:** I tested `parse_query` against all
example queries, ran the full multi-step interaction end-to-end with the example
query, and asserted object identity on the data passed between tools (see State
Management above) before considering each piece done.
