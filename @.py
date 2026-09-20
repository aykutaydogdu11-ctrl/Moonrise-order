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

    # Moonrise combined shorthand
    "S.E.PE": "Sausage, Egg, Poached Egg"
}


# ---------------------------------------------------
# LEARNED CODES
# ---------------------------------------------------

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


# ---------------------------------------------------
# CORRECT & LEARN
# ---------------------------------------------------

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

    # Do not keep saving the exact same correction
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

    # Prevent the prompt/history file growing forever.
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

    # Give the AI only the most recent examples.
    # We do not need all 100 on every request.
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


# ---------------------------------------------------
# UNKNOWN PARSER
# ---------------------------------------------------

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

                # Stop if another section begins
                if code.upper().endswith(":"):
                    break

                if code.lower() in [
                    "none",
                    "n/a",
                    "unknown"
                ]:
                    break

                # Remove simple bullet formatting
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

                    code = code.strip().lstrip("-• ").strip()

                    if code and code not in unknowns:
                        unknowns.append(code)

    return unknowns


# ---------------------------------------------------
# PAGE
# ---------------------------------------------------

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
    min-height: 320px;
    box-sizing: border-box;
    font-family: Arial, sans-serif;
    font-size: 16px;
    line-height: 1.5;
    padding: 12px;
}

input[type="text"] {
    padding: 10px;
    font-size: 15px;
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
Check the order below. If anything is wrong,
edit it before pressing Correct & Learn.
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

<h3>Teach Moonrise Codes</h3>

<p class="help">

These shorthand codes were not recognised.
Teach Moonrise what they mean.

</p>


{% for code in unknowns %}

<div style="margin-bottom:15px;">

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
Save Code
</button>

</form>

</div>

{% endfor %}

</div>

{% endif %}


</body>

</html>
"""


# ---------------------------------------------------
# MAIN ROUTE
# ---------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def home():

    result = ""
    unknowns = []
    saved = ""

    if request.method == "POST":

        action = request.form.get("action")


        # -------------------------------------------
        # LEARN A SHORT CODE
        # -------------------------------------------

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


        # -------------------------------------------
        # CORRECT & LEARN
        # -------------------------------------------

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
                        "as an example on future orders."
                    )

                result = corrected

                unknowns = get_unknowns(
                    corrected
                )


        # -------------------------------------------
        # READ PHOTO
        # -------------------------------------------

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


                    prompt = f"""
You are reading handwritten cafe orders for Moonrise.

Your job is to TRANSCRIBE AND INTERPRET the order accurately.

Do not invent products.

Do not silently remove readable handwriting.


KNOWN MOONRISE CODES:

{code_text}


IMPORTANT:

The known codes above have priority over guesses.

Moonrise staff use shorthand.

Some shorthand may contain dots.

Therefore:

A dot is OFTEN used to separate products,
but a dot is NOT ALWAYS a product separator.

Before splitting text at dots, first check whether
the complete handwritten sequence matches a known
Moonrise code or a previously learned pattern.


VERY IMPORTANT EXAMPLE:

S.E.PE

is a known Moonrise combined shorthand.

It means:

Sausage
Egg
Poached Egg

It must NOT be interpreted as Scrambled Egg.

It must NOT be split incorrectly just because
there are dots in the shorthand.


Another example:

C . L . Bottle

means three separate drinks:

White Coffee
Latte
Bottle drink


The difference must be decided using:

1. Known Moonrise codes
2. The handwriting layout
3. Spaces around separators
4. Previous human corrections
5. Order context


GENERAL RULES:

C = White Coffee
BC = Black Coffee
L = Latte

Do not change C into BC.

PE = Poached Egg.

SE = Scrambled Egg.

S = Sausage.

E = Egg.


QUANTITIES:

2E = 2 Eggs
3B = 3 Bacon
L x2 = 2 Lattes

Numbers normally indicate quantity.

Numbers are not unknown product codes.


FOOD PHRASES:

Spaces can be part of one complete food instruction.

For example:

SE ON 2 TST (BROWN)

means:

Scrambled Egg on 2 Brown Toast

SE = Scrambled Egg
ON = connector
2 = quantity
TST = Toast
BROWN = Brown Toast

Do not put ON, TST, BROWN or the quantity
into UNKNOWN when they form a normal food phrase.


TOAST EXAMPLE:

Cheese on 2 TST

means:

Cheese on 2 Toast


SET MENUS:

Hope 1
Hope 2
Hope 3
Hope 4

are set-menu items.

Keep modifications directly underneath
the item they belong to.


Example:

Hope 1
No S -> B

means:

Hope 1
No Sausage -> Bacon

If the handwriting itself uses the shorthand,
you may preserve the modification as:

No S -> B

Do not attach it to another item.


TABLE NUMBER:

A circled number is usually the table number.

For example, a circled 13 should normally produce:

TABLE:
13


UNKNOWN RULES:

If shorthand is not in KNOWN MOONRISE CODES
and its meaning cannot safely be established,
do NOT invent its meaning.

Keep the exact readable shorthand in the order
and also put the exact code in UNKNOWN.

Unknown codes must never cause other readable
parts of the order to disappear.

UNKNOWN should contain only actual unknown
product shorthand.

Do not put:

quantities,
ON,
TST,
BROWN,
table numbers,
or complete normal instructions

into UNKNOWN.


PREVIOUS HUMAN CORRECTIONS:

The examples below are orders that Moonrise staff
previously corrected.

Use them as examples of Moonrise handwriting,
shorthand and order structure.

A human correction has priority over an old
AI interpretation.

{correction_text}


IMPORTANT LEARNING RULE:

Do not blindly copy a previous order.

Previous corrections are examples.

Use them only when the current handwriting
actually supports the same interpretation.


OUTPUT FORMAT:

Return exactly these sections:


DRINKS:

List drinks only.

One drink per line.


ITEMS:

Number each separate food order.

Example:

1- Sausage
   Egg
   Poached Egg

2- Hope 1
   No S -> B

3- Cheese on 2 Toast


Keep modifications underneath the item
they belong to.


TABLE:

Write only the table number.


UNKNOWN:

Write each unknown shorthand code.

If none:

None


FINAL CHECK BEFORE ANSWERING:

Check the image again.

Make sure every readable order line appears
somewhere in the result.

Do not add a product just because it appeared
in a previous correction.

Do not omit readable products.

Do not guess unknown shorthand.
"""


                    response = client.responses.create(

                        model="gpt-5.4-nano",

                        input=[
                            {
                                "role": "user",

                                "content": [
                                    {
                                        "type":
                                        "input_text",

                                        "text":
                                        prompt
                                    },

                                    {
                                        "type":
                                        "input_image",

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


                    result = (
                        response.output_text
                    )

                    unknowns = (
                        get_unknowns(result)
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


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
