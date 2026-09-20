from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json
import re

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

RULES_FILE = "learned_codes.json"


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
# UNKNOWN HELPERS
# ============================================================

def clean_unknown(code):
    code = code.strip()
    code = code.lstrip("-• ").strip()

    # Remove accidental numbering such as "1- SW"
    code = re.sub(
        r"^\d+\s*[-.)]?\s*",
        "",
        code
    ).strip()

    return code


def split_unknown(value):
    """
    If AI accidentally writes:
        Cap.SW
        Cap / SW
        Cap, SW
    split them into separate unknown codes.
    """

    value = clean_unknown(value)

    if not value:
        return []

    known = load_codes()

    # If the complete code itself has been learned,
    # don't split it.
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
    Read only the UNKNOWN section produced by the AI.
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

        # Stop if somehow another heading appears.
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
# UPDATE CURRENT ORDER AFTER TEACHING A CODE
# ============================================================

def replace_standalone_code(text, code, meaning):
    """
    Replace a standalone shorthand without replacing letters
    inside normal words.

    Example:
        SW -> Sparkling Water
        Cap -> Cappuccino
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

    # Make sure UNKNOWN exists and says None if empty.
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
# TRANSCRIBE THE WHOLE PAPER BEFORE INTERPRETING IT
# ============================================================

TRANSCRIPTION_PROMPT = """
You are reading a handwritten Moonrise Cafe order ticket.

THIS FIRST STAGE IS TRANSCRIPTION ONLY.

Do not interpret shorthand.
Do not translate shorthand.
Do not expand codes.
Do not decide what a code means.
Do not invent products.

Your job is to inspect the ENTIRE ticket from TOP TO BOTTOM
and transcribe every readable handwritten order line.

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

Inspect every section.

If the paper contains:

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

your transcription must contain ALL of those sections.

Do not stop after E . B . BB . Chips.

The Spanish Omelette section is still part of the order.

==================================================
BOTTOM OF THE TICKET
==================================================

Pay special attention to the lower part of the ticket.

Text immediately above the circled table number is often
another food order.

For example:

Spanish Omelette
No Onion

must not disappear.

Another example:

SE on FS

must not disappear.

==================================================
DOT-SEPARATED HANDWRITING
==================================================

Preserve every readable token.

Example:

L . Can . SW

must contain:

L
Can
SW

Do not omit the final SW.

Example:

B . E . BB . Chips

must contain:

B
E
BB
Chips

==================================================
TABLE NUMBER
==================================================

A circled number near the bottom is normally the table number.

Write it separately as:

TABLE:
12

or whatever number is visible.

==================================================
OUTPUT FORMAT
==================================================

Return only:

SECTION 1:
<exact readable handwriting>

SECTION 2:
<exact readable handwriting>

SECTION 3:
<exact readable handwriting>

Continue until every physical order section is represented.

TABLE:
<number>

Do not add interpretations.

==================================================
FINAL SCAN
==================================================

Before answering, visually scan the image AGAIN from top
to bottom.

Check the final section immediately above the table number.

Check the final token on every line.

If readable handwriting is missing from your transcription,
add it before answering.
"""


# ============================================================
# BUILD STAGE 2 PROMPT
# ============================================================

def build_interpretation_prompt(transcription, code_text):
    return f"""
You convert a transcription of a Moonrise Cafe handwritten
ticket into a structured order.

The transcription is the source of truth.

You MUST process EVERY transcribed SECTION.

Do not silently delete a section.

==================================================
TRANSCRIPTION
==================================================

{transcription}


==================================================
KNOWN MOONRISE CODES
==================================================

{code_text}


Known shorthand must be displayed using its full meaning.

Examples:

L = Latte
E = Egg
B = Bacon
BB = Baked Beans
SE = Scrambled Egg
S = Sausage

If a code appears in KNOWN MOONRISE CODES, it is NOT unknown.

==================================================
DRINK SECTIONS
==================================================

A drink section can contain several dot-separated drinks.

Example:

L . Cap . SW

If L is known and Cap and SW are unknown:

DRINKS:
1- Latte
2- Cap
3- SW

UNKNOWN:
Cap
SW

Never merge Cap and SW.

Never guess their meanings.

If the transcription contains:

L . Can . SW

the final order must account for all three tokens.

Do not drop the final SW.

==================================================
FOOD COMPONENT SECTIONS
==================================================

Several dot-separated food components in ONE physical
food section normally belong to ONE food order.

Example:

B . E . BB . Chips

means:

1- Bacon
   Egg
   Baked Beans
   Chips

NOT:

1- Bacon
2- Egg
3- Baked Beans
4- Chips

==================================================
HOPE MENUS
==================================================

Hope 1
Hope 2
Hope 3
Hope 4

are set-menu food orders.

A modification immediately underneath belongs to the same
Hope item.

Example:

Hope 4
No E -> B

must become:

Hope 4
No Egg -> Bacon

Example:

Hope 1
No S -> B

must become:

Hope 1
No Sausage -> Bacon

==================================================
MODIFICATIONS
==================================================

Known shorthand inside modifications must also be expanded.

No E -> B
becomes:
No Egg -> Bacon

No S -> B
becomes:
No Sausage -> Bacon

If FO is a learned code meaning Fried onion:

No Bubble -> FO

becomes:

No Bubble -> Fried onion

Do not leave known shorthand unexpanded.

==================================================
NORMAL FOOD NAMES
==================================================

Normal readable food names do NOT need to exist in the
shorthand dictionary.

Preserve them.

Examples:

Spanish Omelette
Cheese Omelette
Chips
Toast
Salad
Sandwich

A modification immediately underneath belongs to that food.

Example transcription:

Spanish Omelette
No Onion

must become one item:

Spanish Omelette
No Onion

Never omit this section merely because it contains normal words.

==================================================
PARTIALLY UNKNOWN PHRASES
==================================================

If part of a phrase is known and part is unknown,
KEEP THE ENTIRE PHRASE.

Example:

SE on FS

SE is known as Scrambled Egg.
FS is unknown.

The item must be:

Scrambled Egg on FS

and:

UNKNOWN:
FS

Do not delete the whole line.

Do not put the whole phrase under UNKNOWN.

Only FS is unknown.

==================================================
UNKNOWN RULES
==================================================

Unknown shorthand must remain visible in the order.

Each unknown shorthand must also appear separately under UNKNOWN.

Correct:

UNKNOWN:
Cap
SW
FS

Wrong:

UNKNOWN:
Cap.SW

Wrong:

UNKNOWN:
Cap SW

Do NOT put known codes under UNKNOWN.

Do NOT put normal English food words under UNKNOWN.

Do NOT put connector words such as:
on
No

under UNKNOWN.

If there are no unknown codes:

UNKNOWN:
None

==================================================
TABLE
==================================================

Use the table number from the transcription.

Do not treat it as an item or quantity.

==================================================
OUTPUT FORMAT
==================================================

Return exactly these four headings:

DRINKS:

ITEMS:

TABLE:

UNKNOWN:


Number each separate drink.

Number each separate FOOD ORDER.

Components of the same food order should be indented
under the same number.

Example:

DRINKS:
1- Latte
2- Cap
3- Can drink

ITEMS:
1- Hope 1
   No Sausage -> Bacon

2- Egg
   Bacon
   Baked Beans
   Chips

3- Spanish Omelette
   No Onion

TABLE:
12

UNKNOWN:
Cap

==================================================
SECTION ACCOUNTING
==================================================

Before answering, compare the final order with the
transcription section by section.

For every SECTION ask:

"Where is this section represented in my final order?"

Every order section must appear in DRINKS or ITEMS.

Example:

SECTION 1:
L . Can . SW

SECTION 2:
Hope 4
No Bubble -> FO

SECTION 3:
SE on FS

TABLE:
11

If FO is already learned, the result must still include
all three sections:

DRINKS:
1- Latte
2- Can drink
3- SW

ITEMS:
1- Hope 4
   No Bubble -> Fried onion

2- Scrambled Egg on FS

TABLE:
11

UNKNOWN:
SW
FS

SECTION 3 may NOT disappear.

==================================================
FINAL CHECK
==================================================

Before answering:

1. Account for every transcribed section.
2. Account for the final token on every line.
3. Do not invent sections absent from the transcription.
4. Do not duplicate Hope menus.
5. Do not silently remove unknown shorthand.
6. Do not remove normal food names.
7. Expand known codes.
8. Keep unknown codes visible.
9. List each unknown shorthand separately.
10. Return only the structured order.
"""


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

        action = request.form.get(
            "action",
            ""
        )


        # ====================================================
        # LEARN UNKNOWN CODE
        # ====================================================

        if action == "learn":

            code = request.form.get(
                "code",
                ""
            ).strip()

            meaning = request.form.get(
                "meaning",
                ""
            ).strip()

            current_order = request.form.get(
                "current_order",
                ""
            )

            if code and meaning:

                save_code(
                    code,
                    meaning
                )

                result = update_current_order(
                    current_order,
                    code,
                    meaning
                )

                unknowns = get_unknowns(
                    result
                )

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

            photo = request.files.get(
                "photo"
            )

            if not photo:
                error = "Please choose an order photo."

            else:

                try:

                    # ========================================
                    # PREPARE IMAGE
                    # ========================================

                    image_bytes = photo.read()

                    image = base64.b64encode(
                        image_bytes
                    ).decode("utf-8")

                    mime = (
                        photo.mimetype
                        or "image/jpeg"
                    )


                    # ========================================
                    # LOAD LEARNED CODES
                    # ========================================

                    codes = load_codes()

                    code_text = "\n".join(
                        key + " = " + value
                        for key, value in codes.items()
                    )


                    # ========================================
                    # STAGE 1
                    # TRANSCRIBE THE ENTIRE PAPER
                    # ========================================

                    transcription_response = (
                        client.responses.create(
                            model="gpt-5.4-nano",
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
                                            "image_url":
                                                "data:"
                                                + mime
                                                + ";base64,"
                                                + image
                                        }
                                    ]
                                }
                            ]
                        )
                    )

                    transcription = (
                        transcription_response.output_text.strip()
                    )


                    # ========================================
                    # STAGE 2
                    # INTERPRET THE TRANSCRIPTION
                    # ========================================

                    interpretation_prompt = (
                        build_interpretation_prompt(
                            transcription,
                            code_text
                        )
                    )

                    interpretation_response = (
                        client.responses.create(
                            model="gpt-5.4-nano",
                            input=[
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": interpretation_prompt
                                        }
                                    ]
                                }
                            ]
                        )
                    )

                    result = (
                        interpretation_response.output_text.strip()
                    )


                    # ========================================
                    # FIND UNKNOWN CODES
                    # ========================================

                    unknowns = get_unknowns(
                        result
                    )


                except Exception as e:

                    error = (
                        "ERROR: "
                        + str(e)
                    )


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

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
