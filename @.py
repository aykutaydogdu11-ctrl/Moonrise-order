from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json
import re
import difflib

import pricing
import sales

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

RULES_FILE = "learned_codes.json"

# The "nano" tier model is fast/cheap but weak at multi-step reasoning like
# this. If results are still poor after this rewrite, try a bigger model
# in the same family by setting the OPENAI_MODEL environment variable.
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-nano")


# ============================================================
# DEFAULT MOONRISE CODES
# Split into DRINK vs FOOD so the deterministic drink-list
# detector below can tell the difference between "L.Can.SW"
# (a real drink list) and "E.B.BB.Chips" (a food plate that
# just happens to also use dot separators).
# ============================================================

DEFAULT_DRINK_CODES = {
    "C": "White Coffee",
    "BC": "Black Coffee",
    "L": "Latte",
    "Cap": "Cappuccino",
    "T": "Tea",
    "Can": "Can drink",
    "Bottle": "Bottle drink",
}

DEFAULT_FOOD_CODES = {
    "E": "Egg",
    "PE": "Poached Egg",
    "SE": "Scrambled Egg",
    "B": "Bacon",
    "S": "Sausage",
    "BB": "Baked Beans",
    "HB": "Hash Brown",
    "Bubble": "Bubble"
}

DEFAULT_CODES = {**DEFAULT_DRINK_CODES, **DEFAULT_FOOD_CODES}

# Lowercased lookup used only to decide "is this token a known FOOD
# code?" — learned/taught codes are NOT added to this set because we
# don't know their category, so they never force a section away from
# the model's own classification.
FOOD_CODE_KEYS_LOWER = {key.lower() for key in DEFAULT_FOOD_CODES}


# ============================================================
# LOAD / SAVE LEARNED CODES
# ============================================================

def load_codes():
    codes = DEFAULT_CODES.copy()

    try:
        with open(RULES_FILE, "r") as f:
            learned = json.load(f)

            if isinstance(learned, dict):
                codes.update(learned)

    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return codes


def save_code(code, meaning):
    learned = {}

    try:
        with open(RULES_FILE, "r") as f:
            learned = json.load(f)

            if not isinstance(learned, dict):
                learned = {}

    except (FileNotFoundError, json.JSONDecodeError):
        pass

    learned[code] = meaning

    with open(RULES_FILE, "w") as f:
        json.dump(
            learned,
            f,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# UNKNOWN HELPERS (used by the "Teach Moonrise" flow)
# ============================================================

def clean_unknown(code):
    code = code.strip()
    code = code.lstrip("-• ").strip()

    code = re.sub(
        r"^\d+\s*[-.)]?\s*",
        "",
        code
    ).strip()

    return code


def split_unknown(value):
    value = clean_unknown(value)

    if not value:
        return []

    known = load_codes()

    if value in known:
        return [value]

    parts = re.split(
        r"\s*(?:\.|/|\||,)\s*",
        value
    )

    parts = [
        clean_unknown(part)
        for part in parts
        if clean_unknown(part)
    ]

    if len(parts) > 1:
        return parts

    return [value]


def get_unknowns(result):
    """
    Read only the UNKNOWN section of a rendered order.
    Learned codes are filtered out automatically.
    """

    unknowns = []
    known = load_codes()

    known_lower = {
        key.lower()
        for key in known.keys()
    }

    lines = result.splitlines()
    inside_unknown = False

    for line in lines:
        stripped = line.strip()

        if stripped.upper() == "UNKNOWN:":
            inside_unknown = True
            continue

        if stripped.upper().startswith("UNKNOWN:"):
            inside_unknown = True

            inline_value = stripped.split(":", 1)[1].strip()

            if inline_value:
                for candidate in split_unknown(inline_value):
                    candidate = clean_unknown(candidate)

                    if (
                        candidate
                        and candidate.lower() not in [
                            "none",
                            "n/a",
                            "unknown"
                        ]
                        and candidate.lower() not in known_lower
                        and candidate not in unknowns
                    ):
                        unknowns.append(candidate)

            continue

        if not inside_unknown:
            continue

        if stripped.endswith(":"):
            break

        if not stripped:
            continue

        if stripped.lower() in [
            "none",
            "n/a",
            "unknown"
        ]:
            continue

        for candidate in split_unknown(stripped):
            candidate = clean_unknown(candidate)

            if (
                candidate
                and candidate.lower() not in known_lower
                and candidate.lower() not in [
                    "none",
                    "n/a",
                    "unknown"
                ]
                and candidate not in unknowns
            ):
                unknowns.append(candidate)

    return unknowns


# ============================================================
# CODE SUBSTITUTION (deterministic — never guessed by the AI)
# ============================================================

def replace_standalone_code(text, code, meaning):
    """
    Replace a standalone shorthand code without touching letters
    inside normal words. e.g. SW -> Sparkling Water, Cap -> Cappuccino.
    """

    pattern = (
        r"(?<![A-Za-z0-9])"
        + re.escape(code)
        + r"(?![A-Za-z0-9])"
    )

    return re.sub(
        pattern,
        meaning,
        text,
        flags=re.IGNORECASE
    )


# ============================================================
# QUANTITY SHORTHAND ("Ex2" = 2 Egg, "Tx2" = 2 Tea, or "2E")
# ============================================================

_QTY_CODE_X = re.compile(r"^([A-Za-z]+)\s*[xX]\s*(\d+)$")
_QTY_CODE_PREFIX = re.compile(r"^(\d+)\s*([A-Za-z]+)$")


def parse_quantity_code(token):
    """
    Detect a quantity-suffixed/prefixed code like "Ex2", "E x2",
    or "2E", and return (base_code, quantity). Returns
    (token, 1) unchanged if there's no quantity notation.
    """

    token = token.strip()

    m = _QTY_CODE_X.match(token)
    if m:
        return m.group(1), int(m.group(2))

    m = _QTY_CODE_PREFIX.match(token)
    if m:
        return m.group(2), int(m.group(1))

    return token, 1


def get_table_number(base_order):
    """Pull the table number out of the TABLE: block of a
    DRINKS:/ITEMS:/TABLE:/UNKNOWN: order text."""

    m = re.search(r"(?im)^TABLE:\s*\n\s*(.+)$", base_order or "")

    if not m:
        return ""

    value = m.group(1).strip()
    return "" if value.lower() == "unknown" else value


# ============================================================
# MANUAL ADD / REMOVE
# Lets staff add ("one more tea") or remove a line from an
# already-read order without re-photographing the paper ticket —
# parses the DRINKS:/ITEMS:/TABLE:/UNKNOWN: text into a small
# structure, edits it, and serializes it back to the same format
# every other part of the app (pricing, learning) already expects.
# ============================================================

def parse_order_text(base_order):
    drinks = []
    items = []
    table = get_table_number(base_order)
    unknown = []

    section = None
    current_item = None

    for raw_line in (base_order or "").splitlines():
        stripped = raw_line.strip()
        upper = stripped.upper()

        if upper == "DRINKS:":
            section = "drinks"
            continue
        if upper == "ITEMS:":
            section = "items"
            current_item = None
            continue
        if upper == "TABLE:":
            section = "table"
            continue
        if upper == "UNKNOWN:":
            section = "unknown"
            continue

        if not stripped:
            continue

        if section == "drinks":
            m = _NUMBERED_LINE_APP.match(stripped)
            if m and m.group(1).strip().lower() != "none":
                drinks.append(m.group(1).strip())

        elif section == "items":
            m = _NUMBERED_LINE_APP.match(stripped)
            if m:
                if m.group(1).strip().lower() == "none":
                    current_item = None
                    continue
                current_item = {"headline": m.group(1).strip(), "modifiers": []}
                items.append(current_item)
            elif current_item is not None:
                current_item["modifiers"].append(stripped)

        elif section == "unknown":
            if stripped.lower() != "none":
                unknown.append(stripped)

    return {"drinks": drinks, "items": items, "table": table, "unknown": unknown}


_NUMBERED_LINE_APP = re.compile(r"^\d+-\s*(.+)$")


def serialize_order(order):
    drinks_block = (
        "\n".join(f"{i + 1}- {d}" for i, d in enumerate(order["drinks"]))
        if order["drinks"] else "None"
    )

    items_lines = []
    for i, it in enumerate(order["items"]):
        items_lines.append(f"{i + 1}- {it['headline']}")
        for mod in it["modifiers"]:
            items_lines.append(f"   {mod}")

    items_block = "\n".join(items_lines) if items_lines else "None"
    unknown_block = "\n".join(order["unknown"]) if order["unknown"] else "None"
    table_block = order["table"] if order["table"] else "Unknown"

    return (
        "DRINKS:\n" + drinks_block + "\n\n"
        "ITEMS:\n" + items_block + "\n\n"
        "TABLE:\n" + table_block + "\n\n"
        "UNKNOWN:\n" + unknown_block
    )


def get_removable_lines(base_order):
    """List of {"key": "drink:1", "label": "Latte"} for every
    current line, used to populate the "Çıkar" dropdown."""

    order = parse_order_text(base_order)
    options = []

    for i, d in enumerate(order["drinks"]):
        options.append({"key": f"drink:{i + 1}", "label": d})

    for i, it in enumerate(order["items"]):
        options.append({"key": f"item:{i + 1}", "label": it["headline"]})

    return options


def update_current_order(current_order, code, meaning):
    """
    When staff teaches a code:
    - replace it in the visible order
    - remove it from UNKNOWN
    - leave other unknown codes alone
    """

    if not current_order:
        return ""

    lines = current_order.splitlines()

    output = []
    inside_unknown = False

    for line in lines:
        stripped = line.strip()

        if stripped.upper() == "UNKNOWN:":
            inside_unknown = True
            output.append("UNKNOWN:")
            continue

        if inside_unknown:
            # A later section header (e.g. "PRICES:", "TOTAL:",
            # from the pricing step appended after UNKNOWN) ends
            # the unknown-codes block — stop treating lines as
            # unknown codes and just pass the rest through as-is.
            if stripped.endswith(":") and stripped.upper() != "UNKNOWN:":
                inside_unknown = False
                output.append(line)
                continue

            if not stripped:
                continue

            if stripped.lower() == "none":
                continue

            candidates = split_unknown(stripped)

            for candidate in candidates:
                if candidate.lower() != code.lower():
                    output.append(candidate)

            continue

        output.append(
            replace_standalone_code(
                line,
                code,
                meaning
            )
        )

    try:
        unknown_index = next(
            i
            for i, line in enumerate(output)
            if line.strip().upper() == "UNKNOWN:"
        )

        # The unknown block ends at the next section header (e.g.
        # "PRICES:") or the end of the text — don't look past it.
        after = output[unknown_index + 1:]
        end_offset = len(after)

        for i, line in enumerate(after):
            s = line.strip()
            if s.endswith(":") and s.upper() != "UNKNOWN:":
                end_offset = i
                break

        remaining = [
            line.strip()
            for line in after[:end_offset]
            if line.strip()
            and line.strip().lower() != "none"
        ]

        if not remaining:
            output = (
                output[:unknown_index + 1]
                + ["None"]
                + after[end_offset:]
            )

    except StopIteration:
        pass

    return "\n".join(output)


# ============================================================
# FUZZY "DID YOU MEAN?" SUGGESTIONS
# Handwriting OCR sometimes misreads a real known code (e.g. it
# reads "Can" as "Gan", or "Bubble" as "Bubbbe"). Instead of
# forcing staff to retype the full meaning every time, suggest
# the closest known code so it can be confirmed with one tap.
# ============================================================

def suggest_correction(token, known_codes):
    """
    Return (matched_code, meaning) if `token` looks like a
    misread of an existing known code, else None. Exact matches
    are excluded (those are already resolved elsewhere).
    """

    token_clean = token.strip()

    if not token_clean:
        return None

    # Single-letter codes (S, C, E, L, B...) are too short to fuzzy
    # match safely — almost any 2-letter unknown shares a letter
    # with one of them. Only fuzzy-match against codes with at
    # least 2 characters.
    lower_to_code = {
        code.lower(): code
        for code in known_codes.keys()
        if len(code) >= 2
    }

    if token_clean.lower() in lower_to_code:
        return None

    matches = difflib.get_close_matches(
        token_clean.lower(),
        list(lower_to_code.keys()),
        n=1,
        cutoff=0.6
    )

    if not matches:
        return None

    matched_code = lower_to_code[matches[0]]
    return matched_code, known_codes[matched_code]


def build_unknown_suggestions(unknowns, known_codes):
    """
    Turn a plain list of unknown code strings into the richer
    structure the template needs to show "Did you mean X?".
    """

    suggestions = []

    for token in unknowns:
        match = suggest_correction(token, known_codes)

        suggestions.append({
            "code": token,
            "suggested_code": match[0] if match else None,
            "suggested_meaning": match[1] if match else None
        })

    return suggestions


# ============================================================
# HTML
# ============================================================

PAGE = """
<!DOCTYPE html>
<html>

<head>

<meta
name="viewport"
content="width=device-width, initial-scale=1">

<title>Moonrise Order App</title>

<style>

body {
    font-family: Arial, sans-serif;
    padding: 20px;
    max-width: 700px;
    margin: auto;
    background: #fafafa;
}

h1 {
    margin-bottom: 25px;
}

.card {
    background: white;
    padding: 18px;
    border-radius: 10px;
    margin-bottom: 20px;
    border: 1px solid #ddd;
}

button {
    padding: 12px 18px;
    font-size: 16px;
    cursor: pointer;
}

.main-button {
    background: #202a1f;
    color: white;
    border: none;
    border-radius: 7px;
}

.order-box {
    white-space: pre-wrap;
    background: #f2f2f2;
    padding: 15px;
    font-size: 18px;
    line-height: 1.55;
    border-radius: 7px;
}

.success {
    background: #e9f5e9;
    padding: 12px;
    border-radius: 7px;
    margin-bottom: 20px;
}

.error {
    background: #fdeaea;
    padding: 12px;
    border-radius: 7px;
    margin-bottom: 20px;
}

.unknown-box {
    margin-bottom: 20px;
    padding-bottom: 15px;
    border-bottom: 1px solid #ddd;
}

input[type="text"] {
    padding: 10px;
    font-size: 16px;
    width: 62%;
    box-sizing: border-box;
}

.help {
    color: #555;
    font-size: 14px;
    line-height: 1.4;
}

</style>

</head>


<body>

<h1>Moonrise Order App</h1>

<p style="margin-top: -15px;">
<a href="/sales" style="color:#333;">Satış Raporu &rarr;</a>
</p>


<div class="card">

<h2>Read Order</h2>

<form
method="POST"
enctype="multipart/form-data">

<input
type="hidden"
name="action"
value="read">

<input
type="file"
name="photo"
accept="image/*"
capture="environment"
required>

<br><br>

<button
type="submit"
class="main-button">

Read Order

</button>

</form>

</div>


{% if saved %}

<div class="success">

<strong>{{ saved }}</strong>

</div>

{% endif %}


{% if error %}

<div class="error">

<strong>{{ error }}</strong>

</div>

{% endif %}


{% if result %}

<div class="card">

<h2>Order</h2>

<div class="order-box">{{ result }}</div>

{% if base_order %}

<form method="POST" style="margin-top: 14px;">

<input type="hidden" name="action" value="save_sale">
<input type="hidden" name="base_order" value="{{ base_order }}">

<label>
Table
<input type="text" name="table" value="{{ table_number }}" style="width:70px">
</label>

<button type="submit" class="main-button">
Sale Kaydet
</button>

</form>


<form method="POST" style="margin-top: 10px; border-top: 1px solid #eee; padding-top: 10px;">

<input type="hidden" name="action" value="add_item">
<input type="hidden" name="base_order" value="{{ base_order }}">

<select name="item_section">
<option value="drink">Drink</option>
<option value="food">Food</option>
</select>

<input
type="text"
name="item_text"
placeholder="e.g. T, Ex2, Lasagne"
style="width:35%">

<input type="number" name="item_qty" value="1" min="1" style="width:55px">

<button type="submit" class="secondary" style="background:#eef0eb;color:#202a1f;border:1px solid #d9ddd5;">
+ Ürün Ekle
</button>

</form>


{% if removable_lines %}

<form method="POST" style="margin-top: 10px;">

<input type="hidden" name="action" value="remove_item">
<input type="hidden" name="base_order" value="{{ base_order }}">

<select name="remove_line">
{% for opt in removable_lines %}
<option value="{{ opt.key }}">{{ opt.label }}</option>
{% endfor %}
</select>

<button type="submit" class="secondary" style="background:#fdeaea;color:#9d2f2f;border:1px solid #f0d0d0;">
Çıkar
</button>

</form>

{% endif %}

{% endif %}

</div>

{% endif %}


{% if unknowns %}

<div class="card">

<h2>Teach Moonrise</h2>

<p class="help">
I found shorthand codes that I do not know.
Teach me what each one means.
</p>


{% for item in unknowns %}

<div class="unknown-box">

<strong>{{ item.code }}</strong>

{% if item.suggested_meaning %}

<p class="help">
Did you mean <strong>{{ item.suggested_code }}</strong>
({{ item.suggested_meaning }})?
</p>

<form method="POST" style="margin-bottom: 10px;">

<input type="hidden" name="action" value="learn">
<input type="hidden" name="code" value="{{ item.code }}">
<input type="hidden" name="current_order" value="{{ base_order }}">
<input type="hidden" name="meaning" value="{{ item.suggested_meaning }}">

<button type="submit" class="main-button">
Yes, same as {{ item.suggested_code }}
</button>

</form>

{% endif %}

<br>

<form method="POST">

<input
type="hidden"
name="action"
value="learn">

<input
type="hidden"
name="code"
value="{{ item.code }}">

<input
type="hidden"
name="current_order"
value="{{ base_order }}">

<input
type="text"
name="meaning"
placeholder="What does {{ item.code }} mean?"
required>

<button
type="submit"
class="main-button">

Save

</button>

</form>

</div>

{% endfor %}

</div>

{% endif %}


{% if price_unknowns %}

<div class="card">

<h2>Fiyatı Onayla</h2>

<p class="help">
Fiyatını eminlik ile bulamadığım ürünler var. Doğrusunu seç
veya elle fiyat gir — bir daha aynı yazımda sormam.
</p>


{% for item in price_unknowns %}

<div class="unknown-box">

<strong>{{ item.raw_text }}</strong>

{% for s in item.suggestions %}

<form method="POST" style="margin-bottom: 6px; margin-top: 6px;">

<input type="hidden" name="action" value="learn_price">
<input type="hidden" name="raw_text" value="{{ item.raw_text }}">
<input type="hidden" name="base_order" value="{{ base_order }}">
<input type="hidden" name="matched_name" value="{{ s.name }}">
<input type="hidden" name="price" value="{{ '%.2f'|format(s.price) }}">

<button type="submit" class="main-button">
Bu: {{ s.name }} (£{{ '%.2f'|format(s.price) }})
</button>

</form>

{% endfor %}

<br>

<form method="POST">

<input type="hidden" name="action" value="learn_price">
<input type="hidden" name="raw_text" value="{{ item.raw_text }}">
<input type="hidden" name="base_order" value="{{ base_order }}">

<input
type="text"
name="matched_name"
placeholder="Ürün adı (opsiyonel)"
style="width:30%">

<input
type="text"
name="price"
placeholder="£ fiyat"
required
style="width:20%">

<button type="submit" class="main-button">
Kaydet
</button>

</form>

</div>

{% endfor %}

</div>

{% endif %}


</body>

</html>
"""


# ============================================================
# STAGE 1 PROMPT
# TRANSCRIBE ONLY — output is forced into strict JSON so a
# section can never silently vanish the way "SE on FS" did before.
# ============================================================

def build_transcription_prompt(code_text):
    return f"""
You are reading a handwritten Moonrise Cafe order ticket.

THIS FIRST STAGE IS TRANSCRIPTION ONLY.

Do not interpret shorthand.
Do not translate shorthand.
Do not expand codes.
Do not decide what a code means.
Do not invent products.

Your job is to inspect the ENTIRE ticket from TOP TO BOTTOM and
transcribe every readable handwritten order line.

==================================================
ZERO-LOSS RULE
==================================================

Every readable handwritten line must survive.

Never silently omit a line because:
- you do not understand it
- part of it is shorthand
- it is near the bottom of the ticket
- it is immediately above the table number
- you think another section already completed the order

A partially readable line is more useful than a missing line.

==================================================
DO NOT DUPLICATE SECTIONS
==================================================

Each physical, horizontally-separated block of handwriting on
the ticket must appear EXACTLY ONCE in your output, in the same
order it appears on the paper. Never return the same section
text twice, even if you are unsure whether you already
transcribed it — re-read the ticket and count the horizontal
dividers if needed. If a dish (e.g. "Spanish Omelette / No
Onion") appears once on the paper, it must appear once in your
"sections" array, not twice.

==================================================
PHYSICAL SECTIONS
==================================================

Horizontal handwritten lines often separate physical sections.
Inspect every section. If the paper contains:

L . Cap . Can
----------------
Hope 1
No S -> B
----------------
E . B . BB . Chips
----------------
Spanish Omelette
No Onion
----------------
circled 12

you must return FOUR separate section strings (one per
horizontal block), in order, plus the table number. Do not
merge sections and do not stop early — a food section near the
bottom (e.g. "Spanish Omelette / No Onion", or "SE on FS") is
just as important as the first drinks line.

==================================================
DOT-SEPARATED HANDWRITING
==================================================

Preserve every readable token inside a section exactly as
written, including the last one. "L . Can . SW" must keep all
three tokens — do not drop the final SW.

==================================================
KNOWN SHORTHAND CODES (this ticket's vocabulary)
==================================================

This cafe's staff only ever write from this fixed set of short
codes for the drinks/breakfast basics (other dishes are written
as full names, handled separately):

{code_text}

These codes are usually single or double CAPITAL letters. A
handwritten capital letter is easy to misread as a similar-looking
DIGIT, and vice versa — most often:
  S  <->  5
  E  <->  F  (and sometimes 3)
  B  <->  8  (and sometimes 3)
  O  <->  0

When a stroke is genuinely ambiguous between a digit and a
letter, and reading it as one of the codes above would make it a
valid known code, prefer that reading — these tickets are always
written using this exact codebook, so "S" is far more likely than
"5" in a food/drink context. Only keep a digit reading when it
clearly belongs to a number (like a quantity, e.g. "2E", or the
table number), not when it stands alone where a code is expected.

This does not license inventing or expanding codes — it only
resolves genuine stroke-level ambiguity in favor of this known
vocabulary. If a token clearly doesn't match any known code and
isn't a number, transcribe it exactly as written.

==================================================
LETTER-FOR-LETTER FIDELITY FOR SHORT CODES
==================================================

Short handwritten codes (1-3 letters, or all-caps abbreviations)
must be transcribed exactly as the letters appear, even if they
don't spell a real word. Do NOT silently "autocorrect" or expand
a short code into a full dictionary word — e.g. do not turn "L"
into "Latte", and do not turn "Can" into any other word just
because it resembles one. If a letter is ambiguous BETWEEN TWO
LETTERS (not the digit case above), transcribe your best single
reading of the actual strokes, not a guess at what word it
"should" be.

==================================================
TABLE NUMBER
==================================================

A circled number near the bottom is normally the table number.
Return it as a plain string (e.g. "11"). If no table number is
visible, return an empty string.

==================================================
OUTPUT
==================================================

Return your answer using the provided JSON schema:
- "sections": an ordered array of strings, one entry per
  physical section on the ticket, top to bottom, transcribed
  exactly as handwritten (verbatim, multi-line entries are fine).
  Each physical section appears exactly once.
- "table": the table number as a string, or "" if not visible.

Do not add interpretation, commentary, or extra fields.

==================================================
FINAL SCAN
==================================================

Before answering, visually scan the image again from top to
bottom. Check the final section immediately above the table
number, and the final token on every line. If readable
handwriting is missing from your "sections" array, add it
before answering. Also check that no section appears twice, and
double-check any single-letter token against the known codes
above (S vs 5, E vs F, B vs 8) before finalizing.
"""


# ============================================================
# STAGE 2 PROMPT
# CLASSIFY + TOKENIZE ONLY. The model never expands a code to
# its meaning — it only says "this section is a drink list" or
# "this section is a food item", and which raw tokens inside it
# look like shorthand. Python then does the actual code lookup
# from the known-codes dictionary, so the model can no longer
# hallucinate a wrong meaning for a real code.
# ============================================================

def build_classification_prompt(sections, code_text):
    numbered = "\n".join(
        f"{i}: {section}"
        for i, section in enumerate(sections)
    )

    return f"""
You are given the transcribed physical sections of a Moonrise
Cafe order ticket, numbered in the order they appear on the
ticket (top to bottom). Do not reorder, merge, or drop any of
them — your output array must have exactly one entry per input
section, in the same order.

==================================================
SECTIONS
==================================================

{numbered}

==================================================
KNOWN MOONRISE CODES (context only — do not expand them yourself)
==================================================

{code_text}

==================================================
YOUR JOB
==================================================

For EACH section, decide:

1. "type": "drink" if the section is a short list of drink
   codes separated by dots, slashes, or commas — whether or not
   there are spaces around the separator (e.g. both "L . Can . SW"
   and "L.Can.SW" are drink lists). A section that lists FOOD
   codes/items (eggs, bacon, beans, chips, etc.), even if they are
   also separated by dots, is "food", not "drink" — for example
   "E . B . BB . Chips" is food. Otherwise "food".

2. "raw_text": copy the section's text back out exactly as
   given, unmodified, preserving any line breaks.

3. "drink_codes": ONLY for "drink" sections — the individual
   codes in the section, split and in order, exactly as
   handwritten (e.g. "L . Can . SW" -> ["L", "Can", "SW"]).
   Leave this as an empty array for "food" sections.

4. "shorthand_tokens": for "food" sections, list every short
   abbreviation-like token that appears in the raw text which is
   NOT a normal English word (e.g. "FO", "SE", "FS", "BB" are
   shorthand; "No", "on", "Onion", "Hope", "Spanish", "Omelette"
   are normal words and connectors — never list those). Include
   a token even if you don't know what it means. Leave this as
   an empty array if there are no shorthand tokens in that
   section.

DO NOT translate, expand, or guess the meaning of any code
yourself anywhere in your answer — that happens afterwards in a
separate step. Your only job here is classification and copying.

==================================================
EXAMPLE
==================================================

Input sections:
0: L . Can . SW
1: Hope 4
   No Bubble -> FO
2: SE on FS
3: E . B . BB . Chips

Correct output:
[
  {{"type": "drink", "raw_text": "L . Can . SW", "drink_codes": ["L", "Can", "SW"], "shorthand_tokens": []}},
  {{"type": "food", "raw_text": "Hope 4\\nNo Bubble -> FO", "drink_codes": [], "shorthand_tokens": ["FO"]}},
  {{"type": "food", "raw_text": "SE on FS", "drink_codes": [], "shorthand_tokens": ["SE", "FS"]}},
  {{"type": "food", "raw_text": "E . B . BB . Chips", "drink_codes": [], "shorthand_tokens": ["E", "B", "BB"]}}
]

Note that every section is kept (none dropped), "SW" is not
silently lost, "FO"/"SE"/"FS" are flagged as shorthand tokens
without being translated, and the dotted food plate
("E . B . BB . Chips") is correctly classified as food, not
drink, even though it uses dots like a drink list.
"""


# ============================================================
# JSON SCHEMAS FOR STRUCTURED OUTPUT
# ============================================================

TRANSCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "table": {
            "type": "string",
            "description": "The circled table number as written, or an empty string if none is visible."
        },
        "sections": {
            "type": "array",
            "description": "Every physical, horizontally-separated order section, top to bottom, transcribed verbatim, each appearing exactly once.",
            "items": {"type": "string"}
        }
    },
    "required": ["table", "sections"],
    "additionalProperties": False
}


def build_classification_schema(n_sections):
    return {
        "type": "object",
        "properties": {
            "sections": {
                "type": "array",
                # Force exactly one output entry per input section so
                # a section can never be silently dropped at this step.
                "minItems": n_sections,
                "maxItems": n_sections,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["drink", "food"]
                        },
                        "raw_text": {"type": "string"},
                        "drink_codes": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "shorthand_tokens": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "required": [
                        "type",
                        "raw_text",
                        "drink_codes",
                        "shorthand_tokens"
                    ],
                    "additionalProperties": False
                }
            }
        },
        "required": ["sections"],
        "additionalProperties": False
    }


# ============================================================
# DEDUPE TRANSCRIBED SECTIONS
# The vision model occasionally re-reads the same handwritten
# block twice (e.g. "Spanish Omelette / No Onion" appearing as
# two identical sections instead of one). Since a real ticket
# never repeats the exact same section text twice in a row,
# collapse consecutive duplicates before doing anything else.
# ============================================================

def normalize_section_text(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def dedupe_sections(sections):
    deduped = []
    seen_normalized = set()

    for section in sections:
        key = normalize_section_text(section)

        if key and key in seen_normalized:
            continue

        seen_normalized.add(key)
        deduped.append(section)

    return deduped


# ============================================================
# DRINK-LIST DETECTION — deterministic, done in Python.
# The AI sometimes fails to notice that a line like "L.Can.SW"
# (dots with no spaces) is a drink list, and lumps it into
# ITEMS as one blob instead of three separate drinks. Rather
# than trust the AI's own "type" judgement for lines that
# obviously look like a dotted/slashed/comma code list, detect
# that shape ourselves and force it to be split correctly.
#
# BUT: a food plate like "E.B.BB.Chips" has exactly the same
# shape (short dotted tokens), so before forcing anything into
# "drink" we check that none of the tokens are known FOOD
# codes. If any token is a known food code, this is a food
# section and we leave the model's own classification alone.
# ============================================================

def looks_like_drink_list(raw_text):
    text = raw_text.strip()

    if not text:
        return False

    # A modification line ("No Bubble -> FO") or a multi-line
    # food item is never a drink list.
    if "\n" in text or "->" in text or "\u2192" in text:
        return False

    parts = re.split(r"\s*(?:\.|/|\||,)\s*", text)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) < 2:
        return False

    for part in parts:
        # Real drink codes/names are short (1-2 words, e.g. "Can",
        # "Black Coffee"). A long phrase means this probably isn't
        # a simple dotted code list.
        if len(part.split()) > 2 or len(part) > 15:
            return False

        # If any token is a known FOOD code (Egg, Bacon, Baked
        # Beans, etc.), this is a food plate, not a drink list —
        # regardless of dots/shape. This is what stops
        # "E.B.BB.Chips" from being swept into DRINKS. Strip any
        # quantity suffix ("Ex2") before checking, so "Ex2.HB"
        # is still recognized as food.
        base_part, _qty = parse_quantity_code(part)
        if base_part.lower() in FOOD_CODE_KEYS_LOWER:
            return False

    return parts


# ============================================================
# BUILD THE FINAL ORDER — deterministic, done in Python.
# The AI never gets to decide what a known code means; it only
# flagged which tokens are shorthand. This is what fixes the
# "Can -> Black Coffee" / "known code marked UNKNOWN" bugs.
# ============================================================

def build_final_order(sections, table, known_codes):
    known_lower = {
        key.lower(): value
        for key, value in known_codes.items()
    }

    unknowns = []

    def resolve(code, display=None):
        meaning = known_lower.get(code.strip().lower())

        if meaning is None:
            label = display if display else code
            if label not in unknowns:
                unknowns.append(label)
            return label

        return meaning

    drinks_lines = []
    items_lines = []
    drink_num = 0
    item_num = 0

    for section in sections:
        sec_type = section.get("type", "food")
        raw_text = (section.get("raw_text") or "").strip()

        if not raw_text:
            continue

        # Override the AI's classification whenever the raw text is
        # unmistakably a dotted/slashed/comma-separated DRINK code
        # list (this returns False for food plates like
        # "E.B.BB.Chips" — see looks_like_drink_list above).
        forced_drink_codes = looks_like_drink_list(raw_text)

        if forced_drink_codes:
            sec_type = "drink"

        if sec_type == "drink":
            codes = forced_drink_codes or section.get("drink_codes") or []

            if not codes:
                # Safety net in case neither the heuristic nor the
                # model produced a split — don't lose the text.
                codes = re.split(r"\s*(?:\.|/|\||,)\s*", raw_text)
                codes = [c.strip() for c in codes if c.strip()]

            for code in codes:
                drink_num += 1
                base_code, qty = parse_quantity_code(code)
                resolved_name = resolve(base_code, display=code)
                suffix = f" x{qty}" if qty > 1 else ""
                drinks_lines.append(f"{drink_num}- {resolved_name}{suffix}")

        else:
            text = raw_text

            for token in (section.get("shorthand_tokens") or []):
                base_code, qty = parse_quantity_code(token)
                meaning = known_lower.get(base_code.strip().lower())

                if meaning is not None:
                    replacement = meaning if qty == 1 else f"{meaning} x{qty}"
                    text = replace_standalone_code(text, token, replacement)
                elif token not in unknowns:
                    unknowns.append(token)

            lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip()
            ]

            if not lines:
                continue

            item_num += 1
            items_lines.append(f"{item_num}- {lines[0]}")

            for extra in lines[1:]:
                items_lines.append(f"   {extra}")

    drinks_block = "\n".join(drinks_lines) if drinks_lines else "None"
    items_block = "\n".join(items_lines) if items_lines else "None"
    unknown_block = "\n".join(unknowns) if unknowns else "None"
    table_block = table.strip() if table and table.strip() else "Unknown"

    result = (
        "DRINKS:\n" + drinks_block + "\n\n"
        "ITEMS:\n" + items_block + "\n\n"
        "TABLE:\n" + table_block + "\n\n"
        "UNKNOWN:\n" + unknown_block
    )

    return result, unknowns


# ============================================================
# MAIN ROUTE
# ============================================================

@app.route("/", methods=["GET", "POST"])
def home():

    result = ""
    base_order = ""
    unknowns = []
    price_unknowns = []
    sale_items = []
    saved = ""
    error = ""

    if request.method == "POST":

        action = request.form.get("action", "")


        # ====================================================
        # LEARN UNKNOWN CODE
        # ====================================================

        if action == "learn":

            code = request.form.get("code", "").strip()
            meaning = request.form.get("meaning", "").strip()
            current_order = request.form.get("current_order", "")

            if code and meaning:

                save_code(code, meaning)

                base_order = update_current_order(
                    current_order,
                    code,
                    meaning
                )

                unknowns = build_unknown_suggestions(
                    get_unknowns(base_order),
                    load_codes()
                )

                try:
                    result, _total, price_unknowns, sale_items = (
                        pricing.apply_pricing(base_order)
                    )
                except Exception:
                    result = base_order

                saved = (
                    code
                    + " = "
                    + meaning
                    + " learned. Current order updated."
                )


        # ====================================================
        # LEARN / CONFIRM A PRICE
        # ====================================================

        elif action == "learn_price":

            raw_text = request.form.get("raw_text", "").strip()
            matched_name = request.form.get("matched_name", "").strip()
            price_str = request.form.get("price", "").strip()
            base_order = request.form.get("base_order", "")

            try:
                price_value = float(price_str)
            except ValueError:
                price_value = None

            if raw_text and price_value is not None:

                pricing.save_learned_price(
                    raw_text,
                    matched_name or raw_text,
                    price_value
                )

                unknowns = build_unknown_suggestions(
                    get_unknowns(base_order),
                    load_codes()
                )

                try:
                    result, _total, price_unknowns, sale_items = (
                        pricing.apply_pricing(base_order)
                    )
                except Exception as e:
                    result = base_order
                    error = "Pricing step failed: " + str(e)

                saved = (
                    raw_text
                    + " = £"
                    + f"{price_value:.2f}"
                    + " learned."
                )
            else:
                error = "Please enter a valid price."
                result = base_order
                unknowns = build_unknown_suggestions(
                    get_unknowns(base_order),
                    load_codes()
                )


        # ====================================================
        # SAVE SALE (record for turnover/sales-count reporting)
        # ====================================================

        elif action == "save_sale":

            base_order = request.form.get("base_order", "")
            table = request.form.get("table", "").strip()

            unknowns = build_unknown_suggestions(
                get_unknowns(base_order),
                load_codes()
            )

            try:
                result, total, price_unknowns, sale_items = (
                    pricing.apply_pricing(base_order)
                )

                if sale_items:
                    sales.record_sale(
                        table or get_table_number(base_order),
                        sale_items,
                        total
                    )

                    saved = (
                        f"Sale saved: {len(sale_items)} item(s), "
                        f"£{total:.2f} total."
                    )

                    if price_unknowns:
                        saved += (
                            " (Items still needing a price were "
                            "left out of the sale total.)"
                        )
                else:
                    error = (
                        "Nothing priced yet to save — resolve the "
                        "items below first."
                    )

            except Exception as e:
                result = base_order
                error = "Could not save sale: " + str(e)


        # ====================================================
        # ADD ITEM MANUALLY ("one more tea", no re-photographing)
        # ====================================================

        elif action == "add_item":

            base_order = request.form.get("base_order", "")
            section_choice = request.form.get("item_section", "drink")
            entered = request.form.get("item_text", "").strip()

            try:
                qty_field = int(request.form.get("item_qty", "1") or 1)
            except ValueError:
                qty_field = 1

            order = parse_order_text(base_order)

            if entered:
                codes = load_codes()
                known_lower = {k.lower(): v for k, v in codes.items()}
                base_code, embedded_qty = parse_quantity_code(entered)
                meaning = known_lower.get(base_code.strip().lower())

                effective_qty = max(qty_field, 1) * embedded_qty
                resolved_text = meaning if meaning is not None else entered

                if meaning is None and base_code == entered:
                    # A bare unrecognized code (not a full dish name
                    # like "Lasagne") — flag it like OCR-found
                    # unknowns so it still shows up to be taught.
                    if entered not in order["unknown"]:
                        order["unknown"].append(entered)

                display = (
                    f"{resolved_text} x{effective_qty}"
                    if effective_qty > 1 else resolved_text
                )

                if section_choice == "drink":
                    order["drinks"].append(display)
                else:
                    order["items"].append({"headline": display, "modifiers": []})

                base_order = serialize_order(order)
                saved = f"Added: {display}"
            else:
                error = "Please enter an item to add."

            unknowns = build_unknown_suggestions(
                get_unknowns(base_order),
                load_codes()
            )

            try:
                result, _total, price_unknowns, sale_items = (
                    pricing.apply_pricing(base_order)
                )
            except Exception as e:
                result = base_order
                error = (error + " " if error else "") + "Pricing step failed: " + str(e)


        # ====================================================
        # REMOVE A LINE ("customer cancelled the tea")
        # ====================================================

        elif action == "remove_item":

            base_order = request.form.get("base_order", "")
            remove_key = request.form.get("remove_line", "")

            order = parse_order_text(base_order)

            m = re.match(r"^(drink|item)\:(\d+)$", remove_key)

            if m:
                kind, idx_str = m.group(1), m.group(2)
                idx = int(idx_str) - 1

                if kind == "drink" and 0 <= idx < len(order["drinks"]):
                    removed_label = order["drinks"].pop(idx)
                    saved = f"Removed: {removed_label}"
                elif kind == "item" and 0 <= idx < len(order["items"]):
                    removed_label = order["items"].pop(idx)["headline"]
                    saved = f"Removed: {removed_label}"
                else:
                    error = "That line no longer exists."
            else:
                error = "Please choose a line to remove."

            base_order = serialize_order(order)

            unknowns = build_unknown_suggestions(
                get_unknowns(base_order),
                load_codes()
            )

            try:
                result, _total, price_unknowns, sale_items = (
                    pricing.apply_pricing(base_order)
                )
            except Exception as e:
                result = base_order
                error = (error + " " if error else "") + "Pricing step failed: " + str(e)


        # ====================================================
        # READ PHOTO
        # ====================================================

        elif action == "read":

            photo = request.files.get("photo")

            if not photo:
                error = "Please choose an order photo."

            else:

                try:

                    # ========================================
                    # PREPARE IMAGE
                    # ========================================

                    image_bytes = photo.read()
                    image = base64.b64encode(image_bytes).decode("utf-8")
                    mime = photo.mimetype or "image/jpeg"
                    image_data_url = "data:" + mime + ";base64," + image

                    codes = load_codes()
                    code_text = "\n".join(
                        key + " = " + value
                        for key, value in codes.items()
                    )


                    # ========================================
                    # STAGE 1 — TRANSCRIBE (strict JSON)
                    # ========================================

                    transcription_response = client.responses.create(
                        model=MODEL,
                        input=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": build_transcription_prompt(code_text)
                                    },
                                    {
                                        "type": "input_image",
                                        "image_url": image_data_url
                                    }
                                ]
                            }
                        ],
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": "ticket_transcription",
                                "strict": True,
                                "schema": TRANSCRIPTION_SCHEMA
                            }
                        }
                    )

                    transcription_json = json.loads(
                        transcription_response.output_text
                    )

                    sections_raw = transcription_json.get("sections", [])
                    table = transcription_json.get("table", "")

                    # Guard against the model re-reading the same
                    # physical section twice (e.g. one dish appearing
                    # as two identical "sections" entries).
                    sections_raw = dedupe_sections(sections_raw)

                    if not sections_raw:
                        error = (
                            "Could not read any order sections from "
                            "that photo — try a clearer or better-lit "
                            "picture."
                        )

                    else:

                        # ================================
                        # STAGE 2 — CLASSIFY + TOKENIZE
                        # ================================

                        try:
                            classification_prompt = (
                                build_classification_prompt(
                                    sections_raw,
                                    code_text
                                )
                            )

                            schema = build_classification_schema(
                                len(sections_raw)
                            )

                            classification_response = (
                                client.responses.create(
                                    model=MODEL,
                                    input=[
                                        {
                                            "role": "user",
                                            "content": [
                                                {
                                                    "type": "input_text",
                                                    "text": classification_prompt
                                                }
                                            ]
                                        }
                                    ],
                                    text={
                                        "format": {
                                            "type": "json_schema",
                                            "name": "ticket_classification",
                                            "strict": True,
                                            "schema": schema
                                        }
                                    }
                                )
                            )

                            classification_json = json.loads(
                                classification_response.output_text
                            )

                            sections_out = classification_json.get(
                                "sections",
                                []
                            )

                            # Belt-and-braces: if the model still
                            # returned the wrong number of sections,
                            # pad with the raw text instead of losing
                            # anything.
                            if len(sections_out) != len(sections_raw):
                                fixed = []

                                for i, raw in enumerate(sections_raw):
                                    if i < len(sections_out):
                                        fixed.append(sections_out[i])
                                    else:
                                        fixed.append({
                                            "type": "food",
                                            "raw_text": raw,
                                            "drink_codes": [],
                                            "shorthand_tokens": []
                                        })

                                sections_out = fixed

                            result, raw_unknowns = build_final_order(
                                sections_out,
                                table,
                                codes
                            )

                            base_order = result

                            unknowns = build_unknown_suggestions(
                                raw_unknowns,
                                codes
                            )

                            # ============================
                            # PRICING (Stage 3)
                            # Runs on the already-resolved
                            # DRINKS:/ITEMS: text. Never
                            # blocks showing the order if
                            # pricing itself fails.
                            # ============================
                            try:
                                result, _total, price_unknowns, sale_items = (
                                    pricing.apply_pricing(base_order)
                                )
                            except Exception as price_err:
                                error = (
                                    (error + " " if error else "")
                                    + "Pricing step failed: "
                                    + str(price_err)
                                )

                        except Exception as classify_err:
                            # Classification failed — fall back to the
                            # raw transcription rather than showing
                            # nothing or guessing.
                            sections_out = [
                                {
                                    "type": "food",
                                    "raw_text": s,
                                    "drink_codes": [],
                                    "shorthand_tokens": []
                                }
                                for s in sections_raw
                            ]

                            result, raw_unknowns = build_final_order(
                                sections_out,
                                table,
                                codes
                            )

                            base_order = result

                            unknowns = build_unknown_suggestions(
                                raw_unknowns,
                                codes
                            )

                            try:
                                result, _total, price_unknowns, sale_items = (
                                    pricing.apply_pricing(base_order)
                                )
                            except Exception:
                                pass

                            error = (
                                "Code lookup step failed, showing raw "
                                "transcription instead: "
                                + str(classify_err)
                            )

                except Exception as e:
                    error = "ERROR: " + str(e)


    return render_template_string(
        PAGE,
        result=result,
        base_order=base_order,
        table_number=get_table_number(base_order),
        removable_lines=get_removable_lines(base_order),
        unknowns=unknowns,
        price_unknowns=price_unknowns,
        saved=saved,
        error=error
    )


# ============================================================
# START APP
# ============================================================

# ============================================================
# SALES REPORT PAGE
# Shows unit counts sold + turnover for today / this week / all
# time. This is a sales count, not a stock/inventory count — it
# only reflects orders staff explicitly saved with "Sale Kaydet".
# ============================================================

SALES_PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonrise Sales Report</title>
<style>
body { font-family: Arial, sans-serif; padding: 20px; max-width: 700px; margin: auto; background: #fafafa; }
h1 { margin-bottom: 10px; }
h2 { margin-top: 28px; }
.card { background: white; padding: 18px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #ddd; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid #eee; }
th { color: #555; font-size: 13px; }
.total-row td { font-weight: bold; border-top: 2px solid #333; }
button { padding: 10px 16px; font-size: 15px; cursor: pointer; border-radius: 7px; border: none; }
.danger { background: #9d2f2f; color: white; }
.secondary { background: #eef0eb; color: #202a1f; }
.success { background: #e9f5e9; padding: 12px; border-radius: 7px; margin-bottom: 20px; }
a { color: #202a1f; }
</style>
</head>
<body>

<h1>Satış Raporu</h1>
<p><a href="/">&larr; Order App</a></p>

{% if saved %}
<div class="success"><strong>{{ saved }}</strong></div>
{% endif %}

{% for period_key, period_label in [("today","Bugün"), ("week","Bu Hafta"), ("all_time","Tüm Zamanlar")] %}
{% set summary = report[period_key] %}

<div class="card">
<h2>{{ period_label }} — £{{ "%.2f"|format(summary.revenue) }} ({{ summary.order_count }} sipariş)</h2>

{% if summary.item_counts %}
<table>
<tr><th>Ürün</th><th>Adet</th><th>Ciro</th></tr>
{% for name, qty, revenue in summary.item_counts %}
<tr><td>{{ name }}</td><td>{{ qty }}</td><td>£{{ "%.2f"|format(revenue) }}</td></tr>
{% endfor %}
</table>
{% else %}
<p class="muted">Henüz satış yok.</p>
{% endif %}

</div>
{% endfor %}

<div class="card">
<form method="POST" style="display:inline">
<input type="hidden" name="action" value="undo_last">
<button type="submit" class="secondary">Son Satışı Geri Al</button>
</form>
<form method="POST" style="display:inline" onsubmit="return confirm('Tüm satış kayıtları silinecek. Emin misin?');">
<input type="hidden" name="action" value="reset_sales">
<button type="submit" class="danger">Tüm Kayıtları Sıfırla</button>
</form>
</div>

</body>
</html>
"""


@app.route("/sales", methods=["GET", "POST"])
def sales_report():

    saved = ""

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "undo_last":
            removed = sales.delete_last_sale()
            saved = "Last sale removed." if removed else "No sale to undo."

        elif action == "reset_sales":
            sales.clear_sales()
            saved = "All sales records cleared."

    return render_template_string(
        SALES_PAGE,
        report=sales.build_report(),
        saved=saved
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
