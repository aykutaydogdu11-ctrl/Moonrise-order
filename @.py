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
# DEFAULT CODES
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

    except Exception:
        pass

    return codes


def save_code(code, meaning):
    learned = {}

    try:
        with open(RULES_FILE, "r") as f:
            learned = json.load(f)

            if not isinstance(learned, dict):
                learned = {}

    except Exception:
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
# UNKNOWN PARSER
# ============================================================

def clean_unknown(code):
    code = code.strip()
    code = code.lstrip("-• ").strip()

    # Remove numbering/quantity if AI accidentally includes it.
    code = re.sub(
        r"^\d+\s*[-xX]?\s*",
        "",
        code
    ).strip()

    return code


def split_unknown(code):
    """
    If AI accidentally returns:
    Cap.SW
    Cap / SW
    Cap, SW

    split them into separate codes.
    """

    code = clean_unknown(code)

    if not code:
        return []

    known = load_codes()

    # If the complete thing is already a learned code,
    # do not split it.
    if code in known:
        return [code]

    parts = re.split(
        r"\s*(?:\.|/|\||,)\s*",
        code
    )

    parts = [
        clean_unknown(part)
        for part in parts
        if clean_unknown(part)
    ]

    if len(parts) > 1:
        return parts

    # If AI writes "Cap SW" on one UNKNOWN line,
    # split two short code-like tokens.
    words = code.split()

    if (
        len(words) == 2
        and all(len(word) <= 5 for word in words)
    ):
        return words

    return [code]


def get_unknowns(result):
    unknowns = []
    known = load_codes()

    lines = result.splitlines()
    inside_unknown = False

    for line in lines:
        stripped = line.strip()

        if stripped.upper() == "UNKNOWN:":
            inside_unknown = True
            continue

        if stripped.upper().startswith("UNKNOWN:"):
            inside_unknown = True

            value = stripped.split(":", 1)[1].strip()

            if value:
                for candidate in split_unknown(value):
                    if (
                        candidate
                        and candidate not in known
                        and candidate.lower()
                        not in ["none", "n/a", "unknown"]
                        and candidate not in unknowns
                    ):
                        unknowns.append(candidate)

            continue

        if inside_unknown:
            if (
                stripped.endswith(":")
                and stripped.upper() != "UNKNOWN:"
            ):
                inside_unknown = False
                continue

            if not stripped:
                continue

            if stripped.lower() in [
                "none",
                "n/a",
                "unknown"
            ]:
                continue

            for candidate in split_unknown(stripped):
                if (
                    candidate
                    and candidate not in known
                    and candidate not in unknowns
                ):
                    unknowns.append(candidate)

    return unknowns


# ============================================================
# REPLACE ONE LEARNED CODE IN CURRENT ORDER
# ============================================================

def replace_code_in_line(line, code, meaning):
    """
    Replace only standalone shorthand.

    E -> Egg
    B -> Bacon
    Cap -> Cappuccino

    Will not replace letters inside normal words.
    """

    pattern = (
        r"(?<![A-Za-z0-9])"
        + re.escape(code)
        + r"(?![A-Za-z0-9])"
    )

    return re.sub(
        pattern,
        meaning,
        line,
        flags=re.IGNORECASE
    )


def update_current_order(current_order, code, meaning):
    """
    After user teaches a code:

    1. Replace that code in the visible order.
    2. Remove it from UNKNOWN.
    3. Keep other unknowns.
    4. If no unknowns remain -> UNKNOWN: None
    """

    if not current_order:
        return ""

    lines = current_order.splitlines()

    output = []
    inside_unknown = False
    unknown_found = False

    for line in lines:
        stripped = line.strip()

        if stripped.upper() == "UNKNOWN:":
            inside_unknown = True
            unknown_found = True
            output.append("UNKNOWN:")
            continue

        if inside_unknown:
            # There should normally be no section after UNKNOWN,
            # but protect against it anyway.
            if (
                stripped.endswith(":")
                and stripped.upper() != "UNKNOWN:"
            ):
                inside_unknown = False
                output.append(
                    replace_code_in_line(
                        line,
                        code,
                        meaning
                    )
                )
                continue

            if not stripped:
                continue

            candidates = split_unknown(stripped)

            remaining = []

            for candidate in candidates:
                if candidate.lower() != code.lower():
                    remaining.append(candidate)

            for candidate in remaining:
                if candidate.lower() not in [
                    "none",
                    "n/a",
                    "unknown"
                ]:
                    output.append(candidate)

            continue

        # Everywhere outside UNKNOWN:
        line = replace_code_in_line(
            line,
            code,
            meaning
        )

        output.append(line)

    if unknown_found:
        unknown_index = None

        for i, line in enumerate(output):
            if line.strip().upper() == "UNKNOWN:":
                unknown_index = i
                break

        if unknown_index is not None:
            remaining_unknowns = []

            for line in output[unknown_index + 1:]:
                value = line.strip()

                if value and value.lower() != "none":
                    remaining_unknowns.append(value)

            if not remaining_unknowns:
                output = (
                    output[:unknown_index + 1]
                    + ["None"]
                )

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
    line-height: 1.5;
    border-radius: 6px;
}

.success {
    background: #e8f5e9;
    padding: 12px;
    margin-bottom: 20px;
    border-radius: 6px;
}

.unknown-box {
    margin-bottom: 20px;
    padding-bottom: 15px;
    border-bottom: 1px solid #ddd;
}

input[type="text"] {
    padding: 10px;
    font-size: 16px;
    width: 60%;
    box-sizing: border-box;
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


{% if result %}

<div class="card">

<h2>Order</h2>

<div class="order-box">{{ result }}</div>

</div>

{% endif %}


{% if unknowns %}

<div class="card">

<h2>Teach Moonrise</h2>

<p>
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
# MAIN ROUTE
# ============================================================

@app.route("/", methods=["GET", "POST"])
def home():

    result = ""
    unknowns = []
    saved = ""

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
        # READ ORDER
        # ====================================================

        elif action == "read":

            photo = request.files.get(
                "photo"
            )

            if photo:

                try:

                    image = base64.b64encode(
                        photo.read()
                    ).decode("utf-8")

                    mime = (
                        photo.mimetype
                        or "image/jpeg"
                    )

                    codes = load_codes()

                    code_text = "\n".join(
                        key + " = " + value
                        for key, value
                        in codes.items()
                    )


                    prompt = f"""
You read handwritten order tickets for Moonrise Cafe.

Your task has TWO stages:

STAGE 1:
Read exactly what is physically written.

STAGE 2:
Interpret the handwriting using Moonrise's known
shorthand dictionary.

Accuracy is more important than guessing.

Never invent a product.

Never duplicate a handwritten item.

Never silently remove readable handwriting.


==================================================
KNOWN MOONRISE CODES
==================================================

{code_text}


If a shorthand code exists above, ALWAYS display
its FULL MEANING in the final order.

Examples:

L = Latte

Therefore:

L

must display as:

Latte


E = Egg

Therefore E must display as Egg.


B = Bacon

Therefore B must display as Bacon.


BB = Baked Beans

Therefore BB must display as Baked Beans.


==================================================
CRITICAL RULE: DRINK ROW
==================================================

The TOP handwritten section of a Moonrise ticket
can contain several drinks written horizontally.

Dots between drink shorthand separate drinks.

For example, if the top section physically says:

L . Cap . SW

this is THREE drinks:

L
Cap
SW


If L is known but Cap and SW are not known,
the final result MUST be:

DRINKS:
1- Latte
2- Cap
3- SW


UNKNOWN:
Cap
SW


NEVER combine:

Cap.SW

NEVER combine:

Cap SW

NEVER output:

Capp. Sw

NEVER guess that Cap means Cappuccino.

NEVER guess that SW means Sparkling Water.

Until staff teach the code, preserve the exact
unknown shorthand.


==================================================
QUANTITY RULE
==================================================

Do not invent a quantity.

A handwritten capital L can look like a 2.

If the mark is a known code L and is followed by
other drink codes, prefer L = Latte when the
handwriting shape supports L.

Do NOT automatically turn handwritten L into 2.


==================================================
CRITICAL RULE: FOOD ROW
==================================================

Dots in a FOOD section do NOT necessarily mean
different numbered customer orders.

Several components written horizontally can belong
to ONE plate/order.

Example:

B . E . BB . Chips

means ONE FOOD ORDER containing:

Bacon
Egg
Baked Beans
Chips


The correct output is:

ITEMS:
1- Bacon
   Egg
   Baked Beans
   Chips


It is NOT:

1- Bacon
2- Egg
3- Baked Beans
4- Chips


Do not create a Hope menu unless the word Hope is
actually handwritten in that physical section.


==================================================
CRITICAL RULE: PHYSICAL SECTIONS
==================================================

Pay attention to horizontal lines drawn across the
ticket.

A horizontal line can separate:

drinks
food
another food/set-menu order
table number


Do not move handwriting from one physical section
into another.

Do not duplicate a section.

If "Hope 4" appears once on the ticket,
output "Hope 4" ONCE.

Never output the same Hope 4 twice unless it is
physically written twice.


==================================================
HOPE SET MENUS
==================================================

Hope 1
Hope 2
Hope 3
Hope 4

are set-menu items.

An instruction directly underneath a Hope menu
belongs to that same item.


Example handwriting:

Hope 4
No E -> B


must become:

2- Hope 4
   No Egg -> Bacon


NOT:

2- Hope 4
3- Egg
   Bacon


NOT:

2- Hope 4
3- Hope 4


The modification is part of the Hope 4 order.


==================================================
MODIFICATIONS MUST USE FULL WORDS
==================================================

Known shorthand inside a modification must also
be expanded.

Example:

No E -> B

E = Egg
B = Bacon

Therefore final output MUST be:

No Egg -> Bacon


NEVER:

No E -> B


NEVER:

No Egg -> B


NEVER:

No E -> Bacon


Another example:

No S -> B

must become:

No Sausage -> Bacon


==================================================
UNKNOWN CODES
==================================================

If shorthand is not in KNOWN MOONRISE CODES:

DO NOT GUESS.

Keep the exact shorthand visible in the order.

Also list it under UNKNOWN.


Every unknown shorthand must be on a separate line.


Correct:

UNKNOWN:
Cap
SW


Wrong:

UNKNOWN:
Cap.SW


Wrong:

UNKNOWN:
Cap SW


Wrong:

UNKNOWN:
Capp. Sw


If the image contains:

L . Cap . SW

and only L is known:

DRINKS:
1- Latte
2- Cap
3- SW

UNKNOWN:
Cap
SW


==================================================
NORMAL FOOD WORDS
==================================================

Clearly readable ordinary food words do not need
to be listed as unknown shorthand.

Examples:

Chips
Toast
Cheese
Beans
Mushroom
Tomato
Bread
Butter
Salad


==================================================
TABLE NUMBER
==================================================

A circled number near the bottom of the ticket is
normally the table number.

A circled 13 means:

TABLE:
13


Do not treat it as a quantity or food item.


==================================================
NUMBERING
==================================================

Number each separate DRINK:

DRINKS:
1- Latte
2- Cap
3- SW


Number each separate FOOD ORDER:

ITEMS:
1- Bacon
   Egg
   Baked Beans
   Chips

2- Hope 4
   No Egg -> Bacon


Ingredients/components of the same plate do not
receive separate order numbers.


==================================================
OUTPUT FORMAT
==================================================

Return EXACTLY these sections:

DRINKS:

ITEMS:

TABLE:

UNKNOWN:


Do not create additional headings.

==================================================
ZERO-LOSS READING RULE
==================================================

CRITICAL:

EVERY readable handwritten token must survive
into the final result.

You are NOT allowed to silently omit handwriting
because you do not understand its meaning.

Unknown handwriting is NOT a reason to remove
the line.

If part of a line is known and another part is
unknown:

1. Translate the known codes.
2. Preserve the unknown code EXACTLY as written.
3. Keep the complete line in the order.
4. Add the unknown code to UNKNOWN.


Example:

SE on FS

SE is known:

SE = Scrambled Egg

FS is unknown.

Therefore the correct result is:

Scrambled Egg on FS

and:

UNKNOWN:
FS


NEVER delete the whole line because FS is unknown.


Another example:

B on XYZ

if B = Bacon and XYZ is unknown:

Bacon on XYZ

UNKNOWN:
XYZ


==================================================
UNKNOWN CODE INSIDE A PHRASE
==================================================

An unknown code can appear inside a complete
food phrase.

Do not treat the entire phrase as unknown.

Example:

SE on FS

Do NOT put:

SE on FS

under UNKNOWN.

Do NOT put:

Scrambled Egg on FS

under UNKNOWN.

Only the unknown shorthand itself belongs there:

FS


Keep the complete interpreted phrase under ITEMS:

Scrambled Egg on FS


==================================================
END-OF-LINE CODES MUST NOT DISAPPEAR
==================================================

Pay special attention to the LAST code written
on a handwritten line.

The final token must NEVER be ignored merely
because it appears near the edge of the paper,
after another known code, or immediately before
a horizontal separator line.


Example:

L . Can . SW

contains THREE drinks:

L
Can
SW


If L and Can are known but SW is unknown:

DRINKS:
1- Latte
2- Can drink
3- SW

UNKNOWN:
SW


NEVER return only:

1- Latte
2- Can drink


SW must survive.


==================================================
COUNT TOKENS BEFORE INTERPRETING
==================================================

Before interpreting a handwritten section,
first make an internal inventory of every
readable token from LEFT TO RIGHT.

For example, if the drink section reads:

L . Can . SW

the inventory is:

1. L
2. Can
3. SW

The final DRINKS section must account for all
three tokens.


For a food phrase:

SE on FS

the inventory is:

1. SE
2. on
3. FS

The final result must account for all three:

Scrambled Egg on FS


Do this inventory internally.

Do not show the inventory in the final answer.


==================================================
HORIZONTAL SECTIONS MUST ALL BE PROCESSED
==================================================

Every physical section between horizontal
handwritten separator lines must be examined.

Do NOT skip an entire section.

If the ticket contains:

SECTION 1:
L . Can . SW

SECTION 2:
Hope 4
No Bubble -> FO

SECTION 3:
SE on FS

SECTION 4:
circled 11


then ALL FOUR physical sections must appear
in the interpretation.


If FO has previously been learned as Fried onion
and FS and SW are unknown, the result should be:

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


==================================================
UNKNOWN DISCOVERY CHECK
==================================================

After constructing the order, compare the result
against the handwriting one more time.

For every readable shorthand:

Ask:

"Is this shorthand represented somewhere in
DRINKS, ITEMS, TABLE or UNKNOWN?"

If the answer is NO, the result is incomplete.

Add it back.

If its meaning is unknown:

preserve the original shorthand
AND add it to UNKNOWN.

NEVER solve uncertainty by deleting handwriting.


==================================================
ABSOLUTE NO-SKIP RULE
==================================================

A partially understood line is MORE useful than
a missing line.

Therefore:

PRESERVE FIRST.
INTERPRET SECOND.
ASK THE HUMAN THIRD.

Never:

GUESS OR DELETE.
==================================================
MANDATORY FINAL CHECK
==================================================

Before answering, inspect the image again.

Ask yourself:

1. How many physical sections are separated by
   horizontal lines?

2. Did I read the TOP section as drinks when it
   contains drink shorthand?

3. Did I preserve every drink separately?

4. Did I accidentally combine Cap and SW?

5. Did I accidentally read L as the number 2?

6. Did I group food components from the same row
   into one food order?

7. Did I invent Hope 4 somewhere it was not
   physically written?

8. If Hope 4 is written once, did I output it only
   once?

9. Did I expand BB to Baked Beans?

10. Did I expand modification codes completely?

No E -> B MUST display as:

No Egg -> Bacon

11. Did I list every unknown shorthand separately?

12. Did I guess the meaning of an unknown code?

13. Did I account for the LAST readable token
    on every line?

14. Did I process EVERY physical section between
    horizontal separator lines?

15. Is there any readable shorthand in the image
    that disappeared from the final result?

16. If part of a phrase is unknown, did I preserve
    the whole phrase and put ONLY the unknown code
    under UNKNOWN?

17. For a line such as:

    SE on FS

    if SE is known and FS is unknown, did I return:

    Scrambled Egg on FS

    and:

    UNKNOWN:
    FS

18. NEVER finish the answer while a readable
    handwritten token has been silently omitted.

If yes, remove the guess and preserve the original
shorthand.

Return only the finished order.
"""


                    # ============================================================
# STAGE 1 - TRANSCRIBE THE PAPER EXACTLY
# ============================================================

transcription_prompt = """
You are reading a handwritten Moonrise Cafe order ticket.

THIS IS TRANSCRIPTION ONLY.

Do NOT interpret the order yet.
Do NOT translate shorthand.
Do NOT decide what is a drink or food.
Do NOT expand codes.
Do NOT guess meanings.

Your ONLY job is to copy every readable handwritten
piece of text from the paper.

CRITICAL RULE:

NOTHING READABLE MAY BE OMITTED.

The ticket may contain several physical sections
separated by horizontal handwritten lines.

You MUST inspect the ENTIRE paper from TOP TO BOTTOM.

Do not stop after reading the first few sections.

Pay especially close attention to the LOWER HALF
of the paper.

Text immediately above the table number is still
part of the order and MUST be transcribed.

For example, if the bottom of the paper says:

Spanish Omelette
No Onion

you MUST include both lines.

If another paper says:

SE on FS

you MUST include that line exactly.

If the top says:

L . Cap . SW

include all three tokens.

If another section says:

B . E . BB . Chips

include all four tokens.

Preserve:
- words
- shorthand
- dots
- arrows
- quantities
- modifications
- table numbers

A circled number should be transcribed as:

[CIRCLED: 12]

or whatever number is visible.

Use this format:

SECTION 1:
<handwriting>

SECTION 2:
<handwriting>

SECTION 3:
<handwriting>

Continue for EVERY physical section.

TABLE:
<circled number if present>

Before answering, scan the paper AGAIN from
TOP TO BOTTOM.

Ask yourself:

Did I include the final handwritten section
immediately above the table number?

Did I accidentally skip a section because I
already understood the rest of the order?

Did I include every readable line?

If any readable text is missing, add it.

Return ONLY the transcription.
"""


transcription_response = client.responses.create(
    model="gpt-5.4-nano",
    input=[
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": transcription_prompt
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

transcription = transcription_response.output_text


# ============================================================
# STAGE 2 - INTERPRET THE TRANSCRIPTION
# ============================================================

interpret_prompt = f"""
You convert a transcribed Moonrise Cafe ticket
into a structured order.

IMPORTANT:

The transcription below is the source of truth.

You MUST process EVERY section and EVERY line
in the transcription.

You are NOT allowed to silently remove a section.

==================================================
TRANSCRIPTION
==================================================

{transcription}


==================================================
KNOWN MOONRISE CODES
==================================================

{code_text}


Known codes must be expanded to their full meaning.

Examples:

L = Latte
E = Egg
B = Bacon
BB = Baked Beans
SE = Scrambled Egg
S = Sausage


If a learned code exists in KNOWN MOONRISE CODES,
use its saved meaning.


==================================================
DRINKS
==================================================

Drink shorthand written together in the top
drink section represents separate drinks.

Example:

L . Cap . SW

If L is known and Cap and SW are unknown:

DRINKS:
1- Latte
2- Cap
3- SW

Do not combine Cap and SW.

Unknown drinks must remain visible.


==================================================
FOOD COMPONENT ROWS
==================================================

Several dot-separated food components in ONE
physical section normally belong to ONE food order.

Example:

B . E . BB . Chips

becomes:

1- Bacon
   Egg
   Baked Beans
   Chips

Do NOT number Bacon, Egg, Baked Beans and Chips
as four different food orders.


==================================================
NORMAL FOOD NAMES
==================================================

Normal readable food names must be preserved.

Examples:

Spanish Omelette
Cheese Omelette
Chips
Toast
Sandwich
Salad

Do not remove them merely because they are not
in the shorthand dictionary.


A modification directly underneath a food belongs
to that food.

Example:

Spanish Omelette
No Onion

must become:

3- Spanish Omelette
   No Onion


This is VERY IMPORTANT.

Every transcribed physical food section must
produce an item in ITEMS.


==================================================
HOPE MENUS
==================================================

Hope 1
Hope 2
Hope 3
Hope 4

are food/set-menu items.

A modification immediately underneath belongs
to that Hope item.

Example:

Hope 1
No S -> B

becomes:

Hope 1
No Sausage -> Bacon


Example:

Hope 4
No E -> B

becomes:

Hope 4
No Egg -> Bacon


==================================================
MODIFICATIONS
==================================================

Expand known shorthand inside modifications.

No E -> B

must become:

No Egg -> Bacon


No S -> B

must become:

No Sausage -> Bacon


No Bubble -> FO

If FO is learned as Fried onion, becomes:

No Bubble -> Fried onion


==================================================
PARTIALLY UNKNOWN PHRASES
==================================================

Never delete an entire phrase because one code
is unknown.

Example:

SE on FS

If SE is known and FS is unknown:

Scrambled Egg on FS

and:

UNKNOWN:
FS


Preserve the complete phrase.


==================================================
UNKNOWN CODES
==================================================

Only shorthand whose meaning is genuinely unknown
belongs under UNKNOWN.

Do NOT put a code under UNKNOWN if it exists in
KNOWN MOONRISE CODES.

If FO is already learned as Fried onion:

use Fried onion

and DO NOT put FO under UNKNOWN.


Each unknown code must be listed separately.

Example:

Cap
SW

NOT:

Cap.SW


Normal English food words are NOT unknown codes.

Do NOT put these under UNKNOWN:

Spanish
Omelette
Onion
Chips
Toast
Hope
No
on


==================================================
TABLE
==================================================

Use the circled/table number from the transcription.

Do not treat the table number as an item or quantity.


==================================================
OUTPUT FORMAT
==================================================

Return EXACTLY:

DRINKS:

<numbered drinks>


ITEMS:

<numbered food orders>


TABLE:

<table number>


UNKNOWN:

<each unknown shorthand on its own line>


If there are no unknown shorthand codes:

UNKNOWN:
None


==================================================
SECTION ACCOUNTING CHECK
==================================================

Before answering, count the physical sections in
the transcription.

Every order section must be represented somewhere
in DRINKS or ITEMS.

For example, if transcription contains:

SECTION 1:
L . Cap . Can

SECTION 2:
Hope 1
No S -> B

SECTION 3:
E . B . BB . Chips

SECTION 4:
Spanish Omelette
No Onion

TABLE:
12

then the final result MUST contain:

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


SECTION 4 MUST NOT DISAPPEAR.


Another example:

SECTION 1:
L . Can . SW

SECTION 2:
Hope 4
No Bubble -> FO

SECTION 3:
SE on FS

TABLE:
11

If FO is already learned, the result must include:

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


==================================================
FINAL CHECK
==================================================

Compare your finished result against the
TRANSCRIPTION, section by section.

For SECTION 1:
Where did it appear?

For SECTION 2:
Where did it appear?

Continue until every section is accounted for.

Do NOT finish while an order section is missing.

Do not invent handwriting that is absent from
the transcription.

Return only the finished structured order.
"""


interpret_response = client.responses.create(
    model="gpt-5.4-nano",
    input=[
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": interpret_prompt
                }
            ]
        }
    ]
)

result = interpret_response.output_text

unknowns = get_unknowns(result)


                except Exception as e:

                    result = (
                        "ERROR: "
                        + str(e)
                    )


    return render_template_string(
        PAGE,
        result=result,
        unknowns=unknowns,
        saved=saved
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
