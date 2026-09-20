"""
pricing.py
============================================================
Turns the resolved order text (from app.py's OCR pipeline)
into priced lines + a table total, using menu_prices.json.

Design notes
------------
- Mains/burgers/etc. have NO shorthand codes — staff write the
  dish name, but with frequent spelling mistakes ("Lasange",
  "Lasanya"). So matching here is fuzzy (difflib) against the
  menu's real names, not exact lookup.
- Sandwiches are priced by FILLING (fuzzy matched) x SIZE. Size
  is detected from separate tokens in the same line:
    bgt          -> Baguette
    crb          -> Crusty (bread)      ("Brown crb" -> Brown Crusty, same price)
    sand / sdvc  -> Sandwich
    roll         -> Roll (soft / seedy / crusty-roll are all the Roll price)
  If no size token is found, the item is left UNPRICED rather
  than guessed, and surfaced to staff.
- Jacket Potatoes ("JP ...") are matched two ways: first against
  the 7 preset names (JP Magic, JP Hope Umy, ...), then — if that
  fails — against the presets' ingredient descriptions (e.g. "bb
  and cheese melted" -> Magic's "melted cheese & beans"). If the
  best two matches are close in confidence, it is NOT auto-priced
  — staff previously said they resolve these by judgement call
  ("tuna instead of chicken -> use Tuna's price" etc.), which this
  script cannot safely reproduce, so it is flagged for a human.
- Anything that still doesn't match anything gets listed under
  "PRICE UNKNOWN" in the final output instead of silently being
  priced at £0 or skipped.
============================================================
"""

import json
import os
import re
import difflib


MENU_FILE = os.environ.get("MENU_FILE", "menu_prices.json")
LEARNED_PRICES_FILE = os.environ.get("LEARNED_PRICES_FILE", "learned_prices.json")


# ============================================================
# LEARNED PRICES (taught via the "Confirm Price" screen)
# Keyed by the normalized raw ticket text, e.g. "chicken mayo
# roll" -> {"name": "Chicken Mayo or Plain (roll)", "price": 4.00}
# Checked before any fuzzy matching, so a once-confirmed item is
# never asked about again.
# ============================================================

def load_learned_prices():
    try:
        with open(LEARNED_PRICES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_learned_price(raw_text, name, price):
    learned = load_learned_prices()
    learned[_normalize(raw_text)] = {"name": name, "price": float(price)}

    with open(LEARNED_PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(learned, f, indent=2, ensure_ascii=False)


# ============================================================
# SIZE CODES FOR SANDWICHES
# ============================================================

SIZE_TOKEN_TO_COLUMN = {
    "bgt": "baguette",
    "baguette": "baguette",
    "crb": "crusty",
    "crusty": "crusty",
    "sand": "sand",
    "sdvc": "sand",
    "roll": "roll",
    "soft": "roll",
    "seedy": "roll",
}

# Longest tokens first so "brown crb" doesn't get matched as
# just "brown" before "crb" is checked.
_SIZE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z])(" + "|".join(sorted(SIZE_TOKEN_TO_COLUMN.keys(), key=len, reverse=True)) + r")(?![A-Za-z])",
    re.IGNORECASE
)


def detect_size_column(text):
    """
    Return 'roll' | 'sand' | 'crusty' | 'baguette' | None
    by scanning the raw ticket text for a known size token.
    """
    match = _SIZE_TOKEN_PATTERN.search(text)

    if not match:
        return None

    return SIZE_TOKEN_TO_COLUMN[match.group(1).lower()]


# ============================================================
# LOAD + FLATTEN THE MENU
# ============================================================

def _price_of(entry):
    """menu_prices.json stores an item either as a bare number,
    or as {"desc": ..., "price": ...}. Normalize to a float (or
    None if not priced)."""

    if isinstance(entry, dict):
        return entry.get("price")

    if isinstance(entry, (int, float)):
        return float(entry)

    return None


def _desc_of(entry):
    if isinstance(entry, dict):
        return entry.get("desc", "")

    return ""


def load_menu():
    with open(MENU_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_flat_products(menu):
    """
    Returns:
      flat_items: list of {"name", "price", "category"} for every
                  single-priced dish across the whole menu
                  (breakfasts, mains, burgers, omelettes, drinks,
                  JP presets, sandwich baguettes, etc).
      sandwich_fillings: list of {"name", "prices": {roll,sand,crusty,baguette}}
      jp_presets: list of {"name", "desc"} for the 7 Jacket
                  Potato combos, used for ingredient matching.
    """

    flat_items = []
    sandwich_fillings = []
    jp_presets = []

    for category, block in menu.items():
        if not isinstance(block, dict):
            continue

        items = block.get("items")

        if not isinstance(items, dict):
            continue

        if category == "sandwiches":
            for name, prices in items.items():
                if isinstance(prices, list) and len(prices) == 4:
                    sandwich_fillings.append({
                        "name": name,
                        "prices": {
                            "roll": prices[0],
                            "sand": prices[1],
                            "crusty": prices[2],
                            "baguette": prices[3],
                        }
                    })

            for name, entry in (block.get("baguettes") or {}).items():
                price = _price_of(entry)

                if price is not None:
                    flat_items.append({
                        "name": name,
                        "price": price,
                        "category": "sandwiches"
                    })

            continue

        for name, entry in items.items():
            price = _price_of(entry)

            if category == "jumbo_jacket_potatoes":
                jp_presets.append({
                    "name": name,
                    "desc": _desc_of(entry),
                    "price": price
                })

            if price is not None:
                flat_items.append({
                    "name": name,
                    "price": price,
                    "category": category
                })

    return flat_items, sandwich_fillings, jp_presets


# Loaded once at import time; call reload_menu() if the menu
# file changes (e.g. after uploading a new menu photo).
try:
    _MENU = load_menu()
    FLAT_ITEMS, SANDWICH_FILLINGS, JP_PRESETS = build_flat_products(_MENU)
except FileNotFoundError:
    _MENU = {}
    FLAT_ITEMS, SANDWICH_FILLINGS, JP_PRESETS = [], [], []


def reload_menu():
    global _MENU, FLAT_ITEMS, SANDWICH_FILLINGS, JP_PRESETS
    _MENU = load_menu()
    FLAT_ITEMS, SANDWICH_FILLINGS, JP_PRESETS = build_flat_products(_MENU)


# ============================================================
# FUZZY NAME MATCHING (handles misspellings like "Lasange")
# ============================================================

def _normalize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Staff shorthand for individual words within a dish name (not
# full-dish codes — those are handled separately in app.py). Add
# more here as new shorthand comes up, e.g. "C" / "Chick" for
# "Chicken" in "C Mayo" / "Chick Mayo".
WORD_SYNONYMS = {
    "c": "chicken",
    "chick": "chicken",
}


def _word_tokens(text):
    words = [w for w in _normalize(text).split(" ") if w]
    return [WORD_SYNONYMS.get(w, w) for w in words]


def _best_word_ratio(word, other_words):
    if not other_words:
        return 0.0

    return max(
        difflib.SequenceMatcher(None, word, w).ratio()
        for w in other_words
    )


def _token_f1(target_words, candidate_words):
    """
    Word-level, order-independent, typo-tolerant similarity.
    Fixes two cases plain whole-string ratio misses:
      - a short misspelled word buried in a long dish name
        ("Lasange" vs "Lasagne Classico")
      - ingredient lists written in a different order than the
        menu's description ("bb and cheese melted" vs "melted
        cheese & beans")
    """

    if not target_words or not candidate_words:
        return 0.0

    recall = sum(
        _best_word_ratio(w, candidate_words) for w in target_words
    ) / len(target_words)

    precision = sum(
        _best_word_ratio(w, target_words) for w in candidate_words
    ) / len(candidate_words)

    if precision + recall == 0:
        return 0.0

    return 2 * precision * recall / (precision + recall)


def best_fuzzy_match(raw_text, candidates, cutoff=0.6, ambiguity_margin=0.08):
    """
    candidates: list of (key, display_name) tuples to match against.
    Returns (key, display_name, score) for the best match above
    cutoff, or None.

    Scores by the better of a whole-string ratio and a word-level
    F1 (see _token_f1) so both typos and reordered ingredient
    lists are tolerated. If the top two candidates are within
    `ambiguity_margin` of each other, returns None instead of
    guessing — callers pass a wider margin for cases with several
    look-alike variants (e.g. "Chicken Mayo" vs "Chicken Mayo &
    Bacon") where a close call should go to a human, not a guess.
    """

    norm_target = _normalize(raw_text)
    target_words = _word_tokens(raw_text)

    if not norm_target:
        return None

    scored = []

    for key, name in candidates:
        whole_ratio = difflib.SequenceMatcher(
            None, norm_target, _normalize(name)
        ).ratio()

        token_score = _token_f1(target_words, _word_tokens(name))

        score = max(whole_ratio, token_score)
        scored.append((score, key, name))

    scored.sort(reverse=True)

    if not scored or scored[0][0] < cutoff:
        return None

    if len(scored) > 1 and (scored[0][0] - scored[1][0]) < ambiguity_margin:
        return None

    return scored[0][1], scored[0][2], scored[0][0]


def top_candidates(raw_text, candidates, n=3):
    """
    Like best_fuzzy_match, but returns the top n scored candidates
    regardless of cutoff/ambiguity — used to build "Did you mean
    X or Y?" suggestions for the Confirm Price screen when nothing
    was confident enough to auto-price.
    """

    target_words = _word_tokens(raw_text)
    norm_target = _normalize(raw_text)

    if not norm_target:
        return []

    scored = []

    for key, name in candidates:
        whole_ratio = difflib.SequenceMatcher(
            None, norm_target, _normalize(name)
        ).ratio()
        token_score = _token_f1(target_words, _word_tokens(name))
        scored.append((max(whole_ratio, token_score), key, name))

    scored.sort(reverse=True)
    return scored[:n]


# ============================================================
# RESOLVE A SINGLE DRINK CODE'S PRICE
# (drink codes are already expanded to full names, e.g. "Latte",
# by app.py's existing code-lookup step, before this runs)
# ============================================================

def price_drink(drink_name):
    candidates = [
        (item["name"], item["name"])
        for item in FLAT_ITEMS
        if item["category"] in ("hot_drinks", "cold_drinks", "milkshakes")
    ]

    match = best_fuzzy_match(drink_name, candidates, cutoff=0.5)

    if not match:
        return None

    key, name, score = match
    price = next(i["price"] for i in FLAT_ITEMS if i["name"] == key and i["category"] in ("hot_drinks", "cold_drinks", "milkshakes"))
    return name, price


# ============================================================
# RESOLVE A FOOD ITEM'S PRICE
# ============================================================

def price_jacket_potato(raw_text):
    """
    Try preset name first ("JP Magic"), then ingredient
    description ("bb and cheese melted" -> Magic). Returns
    (name, price, note) or None if no confident single match.
    """

    text_after_jp = re.sub(r"(?i)^\s*jp\b", "", raw_text).strip()

    if not text_after_jp:
        return None

    # 1) Match against preset names directly.
    name_candidates = [(p["name"], p["name"]) for p in JP_PRESETS]
    match = best_fuzzy_match(text_after_jp, name_candidates, cutoff=0.6)

    if match:
        key, name, score = match
        preset = next(p for p in JP_PRESETS if p["name"] == key)
        return name, preset["price"], f"matched JP preset '{name}'"

    # 2) Match against ingredient descriptions.
    desc_candidates = [(p["name"], p["desc"]) for p in JP_PRESETS if p["desc"]]
    match = best_fuzzy_match(text_after_jp, desc_candidates, cutoff=0.35)

    if match:
        key, desc, score = match
        preset = next(p for p in JP_PRESETS if p["name"] == key)
        return preset["name"], preset["price"], f"matched by ingredients to '{preset['name']}' ({desc})"

    return None


def price_sandwich(raw_text):
    """
    Fuzzy-match the filling name, and separately detect the size
    token (bgt/crb/sand/roll) anywhere in the same text. Both are
    required — if either is missing/unclear, return None so the
    item is flagged instead of mispriced.
    """

    size_column = detect_size_column(raw_text)

    if not size_column:
        return None

    # Strip the size word itself out before matching the filling,
    # so "Roll"/"Bgt"/etc don't get scored as part of the dish name.
    filling_text = _SIZE_TOKEN_PATTERN.sub(" ", raw_text)

    filling_candidates = [(f["name"], f["name"]) for f in SANDWICH_FILLINGS]

    # Wider ambiguity margin: several fillings only differ by one
    # extra ingredient ("Chicken Mayo" vs "Chicken Mayo & Bacon"),
    # and guessing the wrong one silently mis-prices the order —
    # better to flag it for a human than guess between look-alikes.
    match = best_fuzzy_match(
        filling_text, filling_candidates, cutoff=0.35, ambiguity_margin=0.15
    )

    if not match:
        return None

    key, name, score = match
    filling = next(f for f in SANDWICH_FILLINGS if f["name"] == key)
    price = filling["prices"][size_column]

    return f"{name} ({size_column})", price, f"sandwich: {name} / {size_column}"


def price_food_item(raw_text):
    """
    Main entry point for pricing one food item's headline text
    (e.g. "Hope 1", "JP Magic", "Chicken Mayo Roll", "Lasange").
    Returns (matched_name, price, note) or None.
    """

    text = raw_text.strip()

    if not text:
        return None

    if re.match(r"(?i)^\s*jp\b", text):
        result = price_jacket_potato(text)
        if result:
            return result

    sandwich_result = price_sandwich(text)
    if sandwich_result:
        return sandwich_result

    # General fuzzy match against every non-drink menu item
    # (breakfasts, omelettes, mains, burgers, salads, etc).
    candidates = [
        (item["name"], item["name"])
        for item in FLAT_ITEMS
        if item["category"] not in ("hot_drinks", "cold_drinks", "milkshakes")
    ]

    match = best_fuzzy_match(text, candidates, cutoff=0.55)

    if not match:
        return None

    key, name, score = match
    price = next(
        i["price"] for i in FLAT_ITEMS
        if i["name"] == key and i["category"] not in ("hot_drinks", "cold_drinks", "milkshakes")
    )
    return name, price, f"matched '{name}'"


def get_suggestions(raw_text, is_drink, n=3):
    """
    Top candidate names+prices for the Confirm Price screen,
    using the same routing as price_drink/price_food_item
    (sandwich filling+size, JP preset/ingredients, or general
    menu match) but without a confidence cutoff.
    """

    text = raw_text.strip()

    if is_drink:
        candidates = [
            (item["name"], item["name"])
            for item in FLAT_ITEMS
            if item["category"] in ("hot_drinks", "cold_drinks", "milkshakes")
        ]
        top = top_candidates(text, candidates, n)
        return [
            {"name": name, "price": next(
                i["price"] for i in FLAT_ITEMS
                if i["name"] == key and i["category"] in ("hot_drinks", "cold_drinks", "milkshakes")
            )}
            for score, key, name in top
        ]

    if re.match(r"(?i)^\s*jp\b", text):
        text_after_jp = re.sub(r"(?i)^\s*jp\b", "", text).strip()
        name_candidates = [(p["name"], p["name"]) for p in JP_PRESETS]
        top = top_candidates(text_after_jp or text, name_candidates, n)
        return [
            {"name": name, "price": next(p["price"] for p in JP_PRESETS if p["name"] == key)}
            for score, key, name in top
        ]

    size_column = detect_size_column(text)

    if size_column:
        filling_text = _SIZE_TOKEN_PATTERN.sub(" ", text)
        filling_candidates = [(f["name"], f["name"]) for f in SANDWICH_FILLINGS]
        top = top_candidates(filling_text, filling_candidates, n)
        return [
            {
                "name": f"{name} ({size_column})",
                "price": next(f["prices"][size_column] for f in SANDWICH_FILLINGS if f["name"] == key)
            }
            for score, key, name in top
        ]

    candidates = [
        (item["name"], item["name"])
        for item in FLAT_ITEMS
        if item["category"] not in ("hot_drinks", "cold_drinks", "milkshakes")
    ]
    top = top_candidates(text, candidates, n)
    return [
        {"name": name, "price": next(
            i["price"] for i in FLAT_ITEMS
            if i["name"] == key and i["category"] not in ("hot_drinks", "cold_drinks", "milkshakes")
        )}
        for score, key, name in top
    ]


# ============================================================
# APPLY PRICING TO AN ALREADY-BUILT ORDER TEXT
# (parses the DRINKS: / ITEMS: blocks that build_final_order()
# in app.py produces, prices each line, and appends a PRICES /
# TOTAL section without touching the original blocks)
# ============================================================

_NUMBERED_LINE = re.compile(r"^\d+-\s*(.+)$")


def _extract_block(result_text, block_name):
    """Pull the lines of one block (e.g. 'DRINKS') out of the
    DRINKS:/ITEMS:/TABLE:/UNKNOWN: formatted text."""

    lines = result_text.splitlines()
    collecting = False
    block_lines = []

    for line in lines:
        stripped = line.strip()

        if stripped.upper() == f"{block_name.upper()}:":
            collecting = True
            continue

        if collecting and stripped.endswith(":") and stripped.upper() in (
            "DRINKS:", "ITEMS:", "TABLE:", "UNKNOWN:"
        ):
            break

        if collecting:
            block_lines.append(line)

    return block_lines


def apply_pricing(result_text):
    """
    Returns (priced_result_text, total, unresolved_details).
    unresolved_details is a list of
      {"raw_text": ..., "suggestions": [{"name","price"}, ...]}
    for items that need a human to confirm the price (used to
    render the "Confirm Price" screen).
    Does not modify DRINKS/ITEMS — appends a PRICES + TOTAL
    section built from the same content.
    """

    learned = load_learned_prices()

    drink_lines = _extract_block(result_text, "DRINKS")
    item_lines = _extract_block(result_text, "ITEMS")

    priced_lines = []
    unresolved_details = []
    total = 0.0

    def resolve(name, is_drink):
        key = _normalize(name)
        taught = learned.get(key)

        if taught:
            return taught["name"], taught["price"], True

        result = price_drink(name) if is_drink else price_food_item(name)

        if result:
            return result[0], result[1], False

        return None

    for line in drink_lines:
        stripped = line.strip()
        m = _NUMBERED_LINE.match(stripped)

        if not m or stripped.lower() == "none":
            continue

        name = m.group(1).strip()
        resolved = resolve(name, is_drink=True)

        if resolved:
            matched_name, price, taught = resolved
            total += price
            tag = " (taught)" if taught else ""
            priced_lines.append(f"{name} — £{price:.2f}{tag}")
        else:
            priced_lines.append(f"{name} — £? (unmatched)")
            unresolved_details.append({
                "raw_text": name,
                "suggestions": get_suggestions(name, is_drink=True)
            })

    # Only headline item lines (e.g. "1- Hope 1") are priced —
    # indented modifier lines ("No Onion", "Cucumber Tomato")
    # describe the same dish and don't add their own price.
    for line in item_lines:
        stripped = line.strip()
        m = _NUMBERED_LINE.match(stripped)

        if not m or stripped.lower() == "none":
            continue

        headline = m.group(1).strip()
        resolved = resolve(headline, is_drink=False)

        if resolved:
            matched_name, price, taught = resolved
            total += price
            tag = " (taught)" if taught else ""
            priced_lines.append(f"{headline} — £{price:.2f}{tag}")
        else:
            priced_lines.append(f"{headline} — £? (unmatched)")
            unresolved_details.append({
                "raw_text": headline,
                "suggestions": get_suggestions(headline, is_drink=False)
            })

    prices_block = "\n".join(priced_lines) if priced_lines else "None"
    unresolved_names = [u["raw_text"] for u in unresolved_details]
    unresolved_block = "\n".join(unresolved_names) if unresolved_names else "None"

    priced_result = (
        result_text.rstrip()
        + "\n\nPRICES:\n" + prices_block
        + f"\n\nTOTAL: £{total:.2f}"
        + "\n\nPRICE UNKNOWN (needs manual price):\n" + unresolved_block
    )

    return priced_result, total, unresolved_details
