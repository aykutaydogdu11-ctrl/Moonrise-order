from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json
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
    # Because this exact code is saved here,
    # its dots do NOT separate different products.
    "S.E.PE": "Sausage, Egg, Poached Egg"
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

    # If exactly the same correction already exists,
    # increase its counter instead of saving a duplicate.
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

    # Keep the latest 100 corrections.
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

    # Only send the latest 20 examples to the AI.
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
# FIND UNKNOWN CODES FROM AI RESULT
# ============================================================

def get_unknowns(result):

    unknowns = []

    lines = result.splitlines()

    for i, line in enumerate(lines):

        stripped = line.strip()

        if stripped.upper() == "UNKNOWN:":

            for next_line in lines[i + 1:]:

                code = next_line.strip()

                if not code:
                    continue

                # Another section has started.
                if code.upper().endswith(":"):
                    break

                if code.lower() in [
                    "none",
                    "n/a",
                    "unknown"
                ]:
                    break

                code = code.lstrip("-• ").strip()

                if code and code not in unknowns:
                    unknowns.append(code)

        elif stripped.upper().startswith("UNKNOWN:"):

            value = stripped.split(":", 1)[1].strip()

            if (
                value
                and
                value.lower()
                not in ["none", "n/a", "unknown"]
            ):

                for code in value.split(","):

                    code = code.strip()
                    code = code.lstrip("-• ").strip()

                    if code and code not in unknowns:
                        unknowns.append(code)

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
You read handwritten cafe orders for Moonrise.

Your job is to accurately read the handwriting,
identify each separate product, and use Moonrise's
learned shorthand codes.

Do NOT invent products.

Do NOT silently remove readable handwriting.


==================================================
KNOWN MOONRISE CODES
==================================================

{code_text}


Known codes have priority over guesses.


==================================================
VERY IMPORTANT DOT SEPARATOR RULE
==================================================

A dot "." is a PRODUCT SEPARATOR by default.

Every readable section between dots must first be
treated as a separate product or shorthand code.

For example:

L . Cap . SW

MUST be read as THREE separate codes:

L
Cap
SW

Never combine Cap and SW.

Never output:

Cap.SW

unless the exact complete code "Cap.SW" exists
in KNOWN MOONRISE CODES.


If L is known and Cap and SW are unknown:

L must still be interpreted using its known meaning.

Cap must remain Cap.

SW must remain SW.

UNKNOWN must then contain:

Cap
SW


Another example:

B . E . BB . Chips

MUST first be read as FOUR separate sections:

B
E
BB
Chips

Do NOT merge:

B with E
E with BB
BB with Chips

or any other sections.


If B and E are known and BB is unknown:

interpret B and E normally.

Keep BB exactly as BB.

"Chips" is an ordinary readable food word and
does not need to be treated as shorthand merely
because it is not in the code dictionary.

UNKNOWN should contain:

BB


==================================================
ONLY EXCEPTION TO THE DOT RULE
==================================================

A sequence containing dots may be treated as a
combined Moonrise shorthand ONLY when the EXACT
complete sequence already exists in:

KNOWN MOONRISE CODES.

For example:

S.E.PE

exists as an exact known Moonrise code.

Therefore:

S.E.PE

can use its saved meaning:

Sausage
Egg
Poached Egg


But:

Cap.SW

does NOT become one code unless the exact complete
text:

Cap.SW

has previously been saved as a known Moonrise code.


CRITICAL:

Do NOT invent combined shorthand.

Do NOT decide that two unknown codes form one code.

Unknown codes on opposite sides of a dot are
ALWAYS separate unknown codes unless their exact
combined sequence already exists in KNOWN
MOONRISE CODES.


==================================================
KNOWN CODE RULES
==================================================

C = White Coffee
BC = Black Coffee
L = Latte

Do NOT change C into BC.

PE = Poached Egg.

SE = Scrambled Egg.

S = Sausage.

E = Egg.

B = Bacon.


==================================================
QUANTITIES
==================================================

Numbers can indicate quantities.

Examples:

2E = 2 Eggs

3B = 3 Bacon

L x2 = 2 Lattes


Numbers are NOT unknown product codes.


==================================================
NORMAL FOOD WORDS
==================================================

Normal readable food words do not automatically
become UNKNOWN codes.

Examples include words such as:

Chips
Cheese
Toast
Brown
White

If a normal food word is clearly readable,
preserve the word.


==================================================
FOOD PHRASES
==================================================

Spaces can form one complete food instruction.

For example:

SE ON 2 TST (BROWN)

means:

Scrambled Egg on 2 Brown Toast


SE = Scrambled Egg

ON = connector word

2 = quantity

TST = Toast

BROWN = Brown Toast


Do not put ON, quantity, TST or BROWN into UNKNOWN
when they clearly form this normal food phrase.


Example:

Cheese on 2 TST

means:

Cheese on 2 Toast


==================================================
SET MENUS
==================================================

Hope 1
Hope 2
Hope 3
Hope 4

are set-menu items.

A modification written directly underneath a
set menu belongs to that set menu.


Example:

Hope 4
No E -> B

must remain together:

Hope 4
No E -> B


Do NOT turn the modification into another item.


==================================================
TABLE NUMBER
==================================================

A circled number is normally the table number.

For example:

a circled 13

means:

TABLE:
13


==================================================
UNKNOWN CODE RULES
==================================================

If shorthand is not in KNOWN MOONRISE CODES
and its meaning cannot safely be established:

DO NOT GUESS.

Keep the exact shorthand visible in the order.

Also list it under UNKNOWN.


Most importantly:

Each unknown code must be listed SEPARATELY.


For example:

Cap . SW

must produce:

UNKNOWN:
Cap
SW


NEVER:

UNKNOWN:
Cap.SW


Another example:

B . E . BB . Chips

if B and E are known and BB is unknown:

UNKNOWN:
BB


Do NOT put "Chips" into UNKNOWN simply because
it is an ordinary food word rather than a saved
short code.


Do NOT put these into UNKNOWN:

quantities
table numbers
connector words
ordinary clearly readable food words
complete food instructions


==================================================
DO NOT LOSE PRODUCTS
==================================================

Every readable section separated by a dot must
appear somewhere in the interpreted order.

Never silently skip a section.

Never allow an unknown section to cause a known
section next to it to disappear.


Example:

L . Cap . SW

If Cap and SW are unknown, L must STILL appear.

All three sections must survive:

L
Cap
SW


==================================================
PREVIOUS HUMAN CORRECTIONS
==================================================

The following are previous corrections made by
Moonrise staff.

Use these examples to better understand Moonrise
orders.

A HUMAN CORRECTION is more reliable than the old
AI interpretation.

However:

Do NOT blindly copy a previous order.

Only use a previous correction when the current
handwriting supports the same interpretation.


{correction_text}


==================================================
OUTPUT FORMAT
==================================================

Return exactly these sections:


DRINKS:

List only drinks here.

Write each separate drink on its own line.

Known drink codes must be converted to their
known product names.

If an unknown shorthand appears on a drink line,
preserve it as a separate product and also put
the shorthand under UNKNOWN.


ITEMS:

Number each separate food order.

Example:

1- Sausage
   Egg
   Poached Egg

2- Hope 1
   No S -> B

3- Cheese on 2 Toast


Keep modifications directly underneath the item
they belong to.


TABLE:

Write only the table number.


UNKNOWN:

Write each unknown shorthand code on a
SEPARATE LINE.

If there are no unknown codes, write:

None


==================================================
FINAL CHECK
==================================================

Before answering:

Look at the image again.

Check every handwritten line.

Check every dot-separated section.

If a dot-separated sequence is NOT an exact known
combined Moonrise code, split it into separate
sections.

Make sure no readable product disappeared.

Make sure unknown codes were not merged together.

Make sure each unknown shorthand appears
separately under UNKNOWN.

Do not guess unknown shorthand.

Do not invent products.
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
