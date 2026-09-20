from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json
import re
from datetime import datetime

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

RULES_FILE = "learned_codes.json"
CORRECTIONS_FILE = "corrections.json"


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
    "Bubble": "Bubble",

    # Exact combined shorthand.
    "S.E.PE": "Sausage, Egg, Poached Egg"
}


# ============================================================
# NORMAL WORDS THAT ARE NOT UNKNOWN CODES
# ============================================================

NORMAL_WORDS = {
    "chips",
    "cheese",
    "toast",
    "brown",
    "white",
    "beans",
    "mushroom",
    "tomato",
    "tomatoes",
    "bread",
    "butter",
    "salad",
    "milk",
    "tea",
    "coffee",
    "water",
    "juice",
    "egg",
    "eggs",
    "bacon",
    "sausage",
    "latte"
}


# ============================================================
# LEARNED CODES
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
# CORRECTIONS
# ============================================================

def load_corrections():

    try:

        with open(CORRECTIONS_FILE, "r") as f:

            data = json.load(f)

            if isinstance(data, list):
                return data

    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return []


def save_correction(original, corrected):

    corrections = load_corrections()

    original = original.strip()
    corrected = corrected.strip()

    for correction in corrections:

        if (
            correction.get("original", "").strip() == original
            and
            correction.get("corrected", "").strip() == corrected
        ):

            correction["times_seen"] = (
                correction.get("times_seen", 1) + 1
            )

            correction["last_seen"] = (
                datetime.now().isoformat(timespec="seconds")
            )

            with open(CORRECTIONS_FILE, "w") as f:

                json.dump(
                    corrections,
                    f,
                    indent=2,
                    ensure_ascii=False
                )

            return

    corrections.append({
        "original": original,
        "corrected": corrected,
        "times_seen": 1,
        "last_seen": datetime.now().isoformat(timespec="seconds")
    })

    corrections = corrections[-100:]

    with open(CORRECTIONS_FILE, "w") as f:

        json.dump(
            corrections,
            f,
            indent=2,
            ensure_ascii=False
        )


def corrections_for_prompt():

    corrections = load_corrections()

    if not corrections:
        return "No previous corrected orders yet."

    recent = corrections[-20:]

    blocks = []

    for number, correction in enumerate(recent, start=1):

        blocks.append(
            f"""
CORRECTION EXAMPLE {number}

AI PREVIOUSLY READ:
{correction.get("original", "")}

HUMAN CORRECTED IT TO:
{correction.get("corrected", "")}
"""
        )

    return "\n".join(blocks)


# ============================================================
# UNKNOWN HELPERS
# ============================================================

def clean_unknown_code(code):

    code = code.strip()
    code = code.lstrip("-• ").strip()

    # Remove accidental quantity at beginning:
    # "2 Cap" -> "Cap"
    code = re.sub(
        r"^\d+\s*[xX]?\s*",
        "",
        code
    ).strip()

    return code


def split_unknown_code(code, known_codes):

    """
    Safety layer.

    If the AI accidentally returns:

        Cap.SW

    under UNKNOWN, split it into:

        Cap
        SW

    UNLESS Cap.SW itself is an exact learned code.
    """

    code = clean_unknown_code(code)

    if not code:
        return []

    # Exact learned combined code must stay together.
    if code in known_codes:
        return [code]

    # Split on dot, slash, pipe or comma.
    parts = re.split(
        r"\s*[./|,]\s*",
        code
    )

    parts = [
        clean_unknown_code(part)
        for part in parts
        if clean_unknown_code(part)
    ]

    if len(parts) > 1:
        return parts

    return [code]


def should_be_unknown(code, known_codes):

    code = code.strip()

    if not code:
        return False

    if code.lower() in [
        "none",
        "n/a",
        "unknown"
    ]:
        return False

    # Known code
    if code in known_codes:
        return False

    # Normal English food word
    if code.lower() in NORMAL_WORDS:
        return False

    # Just a number
    if code.isdigit():
        return False

    return True


# ============================================================
# FIND UNKNOWN CODES FROM AI RESULT
# ============================================================

def get_unknowns(result):

    known_codes = load_codes()

    unknowns = []

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

                candidates = split_unknown_code(
                    value,
                    known_codes
                )

                for candidate in candidates:

                    if (
                        should_be_unknown(
                            candidate,
                            known_codes
                        )
                        and
                        candidate not in unknowns
                    ):
                        unknowns.append(candidate)

            continue

        # Another main section starts
        if (
            inside_unknown
            and
            stripped.endswith(":")
            and
            stripped.upper() != "UNKNOWN:"
        ):
            inside_unknown = False

        if inside_unknown:

            if not stripped:
                continue

            if stripped.lower() in [
                "none",
                "n/a",
                "unknown"
            ]:
                continue

            candidates = split_unknown_code(
                stripped,
                known_codes
            )

            for candidate in candidates:

                if (
                    should_be_unknown(
                        candidate,
                        known_codes
                    )
                    and
                    candidate not in unknowns
                ):
                    unknowns.append(candidate)

    return unknowns


# ============================================================
# HTML PAGE
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

.read-button {
    background: #202a1f;
    color: white;
    border: none;
    border-radius: 7px;
}

.learn-button {
    background: #202a1f;
    color: white;
    border: none;
    border-radius: 7px;
}

textarea {
    width: 100%;
    min-height: 360px;
    box-sizing: border-box;
    font-family: Arial, sans-serif;
    font-size: 16px;
    line-height: 1.5;
    padding: 12px;
}

input[type="text"] {
    padding: 10px;
    font-size: 15px;
    width: 65%;
    box-sizing: border-box;
}

.success {
    background: #e9f5e9;
    padding: 12px;
    border-radius: 7px;
    margin-bottom: 15px;
}

.help {
    color: #555;
    font-size: 14px;
}

.unknown-box {
    margin-bottom: 20px;
    padding-bottom: 15px;
    border-bottom: 1px solid #ddd;
}

</style>

</head>


<body>


<h1>Moonrise Order App</h1>


<div class="card">

<h2>Read Order</h2>

<form method="POST" enctype="multipart/form-data">

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
class="read-button">

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

<p class="help">

Check the order below.

If anything is wrong, edit it and press
Correct & Learn.

</p>


<form method="POST">

<input
type="hidden"
name="action"
value="correct">

<input
type="hidden"
name="original"
value="{{ result }}">


<textarea name="corrected">{{ result }}</textarea>

<br><br>

<button
type="submit"
class="learn-button">

Correct & Learn

</button>

</form>

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
type="text"
name="meaning"
placeholder="What does {{ code }} mean?"
required>

<button type="submit">

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

        action = request.form.get("action")


        # ====================================================
        # LEARN UNKNOWN SHORT CODE
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

            if code and meaning:

                save_code(
                    code,
                    meaning
                )

                saved = (
                    code
                    + " = "
                    + meaning
                    + " saved."
                )


        # ====================================================
        # CORRECT & LEARN COMPLETE ORDER
        # ====================================================

        elif action == "correct":

            original = request.form.get(
                "original",
                ""
            ).strip()

            corrected = request.form.get(
                "corrected",
                ""
            ).strip()

            if original and corrected:

                if original == corrected:

                    saved = (
                        "Order confirmed. "
                        "No correction was needed."
                    )

                else:

                    save_correction(
                        original,
                        corrected
                    )

                    saved = (
                        "Correction saved. "
                        "Moonrise will use it "
                        "on future orders."
                    )

                result = corrected

                unknowns = get_unknowns(
                    corrected
                )


        # ====================================================
        # READ ORDER PHOTO
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
                        or
                        "image/jpeg"
                    )

                    codes = load_codes()

                    code_text = "\n".join(
                        key + " = " + value
                        for key, value
                        in codes.items()
                    )

                    correction_text = (
                        corrections_for_prompt()
                    )


                    # ========================================
                    # AI INSTRUCTIONS
                    # ========================================

                    prompt = f"""
You are reading handwritten cafe order tickets
for Moonrise.

Accuracy is more important than guessing.

Read the physical handwriting first.

Then interpret Moonrise shorthand.

NEVER invent a product.

NEVER silently remove readable handwriting.


==================================================
KNOWN MOONRISE CODES
==================================================

{code_text}


The saved codes above are authoritative.

If a shorthand exactly matches a saved code,
use its saved meaning.


==================================================
CRITICAL: PRODUCT SEPARATORS
==================================================

Handwritten punctuation is used to separate
products.

These can all act as separators:

.
/
|
commas
clear gaps between shorthand codes


A separator normally means:

THE PRODUCT BEFORE IT AND THE PRODUCT AFTER IT
ARE DIFFERENT PRODUCTS.


Example:

L . Cap . SW

means THREE products:

L
Cap
SW


It does NOT mean:

L
Cap.SW


==================================================
VERY IMPORTANT EXAMPLE: QUANTITY + SEPARATORS
==================================================

If handwriting says:

2 Cap.SW

or:

2 Cap . SW

read this as TWO DIFFERENT PRODUCTS:

2 Cap
2 SW


The leading quantity 2 applies to BOTH products
when they are written as a grouped pair like this.


DO NOT output:

2 Cap.SW

DO NOT treat Cap.SW as one unknown code.

DO NOT ask the human:

"What does Cap.SW mean?"


Instead, if Cap and SW are not known:

preserve:

2 Cap
2 SW

and UNKNOWN must be:

Cap
SW


==================================================
MORE QUANTITY EXAMPLES
==================================================

2 C.BC

means:

2 White Coffee
2 Black Coffee


3 Cap.SW

means:

3 Cap
3 SW


2 B.E

normally means:

2 Bacon
2 Egg


However, an EXACT combined shorthand already
saved in KNOWN MOONRISE CODES remains a combined
shorthand.


==================================================
EXACT SAVED COMBINED CODE EXCEPTION
==================================================

There is only one reason to keep dot-separated
shorthand together:

THE EXACT FULL STRING EXISTS IN KNOWN MOONRISE
CODES.


For example:

S.E.PE

is an exact saved code.

Therefore it can use its saved meaning.


But if:

Cap.SW

is NOT an exact saved code:

YOU MUST SPLIT IT:

Cap
SW


Never invent a new combined shorthand.


==================================================
FOOD LINES ALSO USE SEPARATORS
==================================================

For example:

B.F . BB . Chips

must initially be understood as THREE separate
readable components:

B.F
BB
Chips


Do NOT produce:

B.F.BB.Chips


Do NOT produce one UNKNOWN called:

B.F / BB / Chips


Each shorthand candidate must be considered
independently.


If B.F and BB are unknown:

UNKNOWN:

B.F
BB


Chips is an ordinary food word.

Therefore Chips must NOT be put into UNKNOWN.


==================================================
KNOWN CODE RULES
==================================================

C = White Coffee
BC = Black Coffee
L = Latte

PE = Poached Egg
SE = Scrambled Egg
S = Sausage
E = Egg
B = Bacon

Do not change one known code into another.


==================================================
QUANTITIES
==================================================

Numbers indicate quantities when written with
products.

Examples:

2E = 2 Eggs

3B = 3 Bacon

L x2 = 2 Lattes


A number itself is NOT an unknown shorthand.


When ONE quantity is written before several
dot-separated products on the same handwritten
group, apply that quantity to each product unless
the handwriting clearly indicates otherwise.


Example:

2 Cap.SW

=

2 Cap
2 SW


==================================================
NORMAL FOOD WORDS
==================================================

Normal readable English food words are NOT
unknown shorthand codes.

Examples:

Chips
Cheese
Toast
Brown
White
Beans
Mushroom
Tomato
Bread
Butter
Salad


Preserve these as ordinary words.


==================================================
FOOD PHRASES
==================================================

Some spaces form a single food instruction rather
than separate products.

Example:

SE ON 2 TST (BROWN)

means:

Scrambled Egg on 2 Brown Toast


SE = Scrambled Egg
ON = connector
2 = quantity
TST = Toast
BROWN = Brown


Do not list connector words or quantities under
UNKNOWN.


Example:

Cheese on 2 TST

means:

Cheese on 2 Toast


==================================================
SET MENUS AND MODIFICATIONS
==================================================

Hope 1
Hope 2
Hope 3
Hope 4

are set-menu items.


A handwritten instruction immediately underneath
a Hope item belongs to that Hope item.


Example:

Hope 4
No E -> B

MUST remain:

Hope 4
No E -> B


It is ONE menu item with a modification.


DO NOT turn:

No E -> B

into a separate order.


The arrow -> means a substitution/modification.


==================================================
TABLE NUMBER
==================================================

A circled number is normally the table number.

Example:

circled 13

means:

TABLE:
13


Do not interpret the circled table number as a
quantity or product.


==================================================
UNKNOWN CODES
==================================================

If a shorthand code is not known and you cannot
safely determine its meaning:

DO NOT GUESS.

Keep the shorthand exactly as read.

Then put it under UNKNOWN.


CRITICAL:

Each unknown shorthand must appear on its own
line.


Correct:

UNKNOWN:
Cap
SW


Incorrect:

UNKNOWN:
Cap.SW


Incorrect:

UNKNOWN:
Cap / SW


Incorrect:

UNKNOWN:
2 Cap.SW


Quantities must NOT be included in the unknown
code name.


Therefore:

2 Cap

should produce unknown code:

Cap

NOT:

2 Cap


==================================================
DO NOT LOSE KNOWN PRODUCTS
==================================================

Unknown handwriting beside a known product must
never cause the known product to disappear.


Example:

L . Cap . SW

must preserve all three:

Latte
Cap
SW


==================================================
PREVIOUS HUMAN CORRECTIONS
==================================================

These are examples previously corrected by
Moonrise staff:

{correction_text}


Human corrections are useful examples.

But do not blindly copy an old order.

The CURRENT IMAGE always has priority.


==================================================
OUTPUT FORMAT
==================================================

Return EXACTLY these four sections:


DRINKS:

Put drinks here.

Each different drink/product must appear on its
own line.

Include quantities.

Example:

2 Latte
2 Cap
2 SW


ITEMS:

Number each separate food order.

Example:

1- Sausage
   Egg
   Poached Egg

2- Hope 4
   No E -> B

3- Cheese on 2 Toast


Keep modifications underneath their parent item.


TABLE:

Write only the table number.


UNKNOWN:

Each unknown shorthand on its OWN LINE.

Example:

Cap
SW
BB


If there are no unknown shorthand codes:

None


==================================================
MANDATORY FINAL VISUAL CHECK
==================================================

Before returning the answer:

1. Look at the actual image again.

2. Check every handwritten line.

3. Check every dot, slash and separator.

4. Check whether any quantity appears before a
   group of products.

5. If you see something similar to:

   2 Cap.SW

   make sure you have NOT returned Cap.SW as one
   product.

6. Unless Cap.SW exists EXACTLY in the saved code
   dictionary, it must become:

   2 Cap
   2 SW

7. Check food lines separately.

8. If you see:

   B.F . BB . Chips

   do NOT merge all three.

9. Keep Hope menu modifications underneath the
   Hope item.

10. Make sure no readable product disappeared.

11. Make sure UNKNOWN contains individual codes,
    never a collection of codes joined by dots,
    slashes or commas.

12. Do not guess.
"""


                    # ========================================
                    # SEND IMAGE TO OPENAI
                    # ========================================

                    response = client.responses.create(

                        model="gpt-5.4-nano",

                        input=[
                            {
                                "role": "user",

                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": prompt
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


                    result = response.output_text

                    unknowns = get_unknowns(
                        result
                    )


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

    app.run(
        host="0.0.0.0",
        port=10000
                      )
