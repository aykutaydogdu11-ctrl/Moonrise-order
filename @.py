from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json
import re

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

RULES_FILE = "learned_codes.json"

# The "nano" tier model is fast/cheap but weak at multi-step reasoning like
# this. If results are still poor after this rewrite, try a bigger model
# in the same family by setting the OPENAI_MODEL environment variable.
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-nano")


# ============================================================
# DEFAULT MOONRISE CODES
# ============================================================

DEFAULT_CODES = {
    "C": "White Coffee",
    "BC": "Black Coffee",
    "L": "Latte",
    "Can": "Can drink",
    "Bottle": "Bottle drink",

    "E": "Egg",
    "PE": "Poached Egg",
    "SE": "Scrambled Egg",
    "B": "Bacon",
    "S": "Sausage",
    "BB": "Baked Beans",
    "Bubble": "Bubble"
}


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

        remaining = [
            line.strip()
            for line in output[unknown_index + 1:]
            if line.strip()
            and line.strip().lower() != "none"
        ]

        if not remaining:
            output = (
                output[:unknown_index + 1]
                + ["None"]
            )

    except StopIteration:
        pass

    return "\n".join(output)


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

</div>

{% endif %}


{% if unknowns %}

<div class="card">

<h2>Teach Moonrise</h2>

<p class="help">
I found shorthand codes that I do not know.
Teach me what each one means.
</p>


{% for code in unknowns %}

<div class="unknown-box">

<strong>{{ code }}</strong>

<br><br>

<form method="POST">

<input
type="hidden"
name="action"
value="learn">

<input
type="hidden"
name="code"
value="{{ code }}">

<input
type="hidden"
name="current_order"
value="{{ result }}">

<input
type="text"
name="meaning"
placeholder="What does {{ code }} mean?"
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


</body>

</html>
"""


# ============================================================
# STAGE 1 PROMPT
# TRANSCRIBE ONLY — output is forced into strict JSON so a
# section can never silently vanish the way "SE on FS" did before.
# ============================================================

TRANSCRIPTION_PROMPT = """
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
- "table": the table number as a string, or "" if not visible.

Do not add interpretation, commentary, or extra fields.

==================================================
FINAL SCAN
==================================================

Before answering, visually scan the image again from top to
bottom. Check the final section immediately above the table
number, and the final token on every line. If readable
handwriting is missing from your "sections" array, add it
before answering.
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
   codes (usually separated by dots, slashes, or commas, e.g.
   "L . Can . SW"). Otherwise "food".

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

Correct output:
[
  {{"type": "drink", "raw_text": "L . Can . SW", "drink_codes": ["L", "Can", "SW"], "shorthand_tokens": []}},
  {{"type": "food", "raw_text": "Hope 4\\nNo Bubble -> FO", "drink_codes": [], "shorthand_tokens": ["FO"]}},
  {{"type": "food", "raw_text": "SE on FS", "drink_codes": [], "shorthand_tokens": ["SE", "FS"]}}
]

Note that every section is kept (none dropped), "SW" is not
silently lost, and "FO"/"SE"/"FS" are flagged as shorthand
tokens without being translated.
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
            "description": "Every physical, horizontally-separated order section, top to bottom, transcribed verbatim.",
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

    def resolve(code):
        meaning = known_lower.get(code.strip().lower())

        if meaning is None:
            if code not in unknowns:
                unknowns.append(code)
            return code

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

        if sec_type == "drink":
            codes = section.get("drink_codes") or []

            if not codes:
                # Safety net in case the model left this empty —
                # split the raw text ourselves rather than lose it.
                codes = re.split(r"\s*(?:\.|/|\||,)\s*", raw_text)
                codes = [c.strip() for c in codes if c.strip()]

            for code in codes:
                drink_num += 1
                drinks_lines.append(f"{drink_num}- {resolve(code)}")

        else:
            text = raw_text

            for token in (section.get("shorthand_tokens") or []):
                meaning = known_lower.get(token.strip().lower())

                if meaning is not None:
                    text = replace_standalone_code(text, token, meaning)
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
    unknowns = []
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

                result = update_current_order(
                    current_order,
                    code,
                    meaning
                )

                unknowns = get_unknowns(result)

                saved = (
                    code
                    + " = "
                    + meaning
                    + " learned. Current order updated."
                )


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
                                        "text": TRANSCRIPTION_PROMPT
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

                            result, unknowns = build_final_order(
                                sections_out,
                                table,
                                codes
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

                            result, unknowns = build_final_order(
                                sections_out,
                                table,
                                codes
                            )

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
        unknowns=unknowns,
        saved=saved,
        error=error
    )


# ============================================================
# START APP
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
