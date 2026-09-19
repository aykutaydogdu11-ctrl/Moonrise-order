from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

RULES_FILE = "learned_codes.json"

DEFAULT_CODES = {
    "C": "Coffee",
    "BC": "Black Coffee",
    "L": "Latte",
    "Can": "Can drink",
    "Bottle": "Bottle drink",
    "E": "Egg",
    "SE": "Scrambled Egg",
    "B": "Bacon",
    "S": "Sausage",
    "Bubble": "Bubble"
}

def load_codes():
    codes = DEFAULT_CODES.copy()

    try:
        with open(RULES_FILE, "r") as f:
            codes.update(json.load(f))
    except:
        pass

    return codes


def save_code(code, meaning):
    learned = {}

    try:
        with open(RULES_FILE, "r") as f:
            learned = json.load(f)
    except:
        pass

    learned[code] = meaning

    with open(RULES_FILE, "w") as f:
        json.dump(learned, f)


PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Moonrise Order App</title>
</head>

<body style="font-family:Arial;padding:20px;max-width:700px;margin:auto">

<h1>Moonrise Order App</h1>

<h2>Read Order</h2>

<form method="POST" enctype="multipart/form-data">

<input type="hidden" name="action" value="read">

<input
type="file"
name="photo"
accept="image/*"
capture="environment"
required>

<br><br>

<button type="submit">
Read Order
</button>

</form>

{% if result %}

<hr>
<h2>Order</h2>

<div style="white-space:pre-wrap;background:#eee;padding:15px">
{{ result }}
</div>

{% endif %}

{% if unknowns %}

<hr>

<h3>Teach Moonrise</h3>

<p>I found codes I don't know:</p>

{% for code in unknowns %}

<div style="margin-bottom:15px;">

<strong>{{ code }}</strong>

<form method="POST" style="display:inline;">

<input type="hidden"
name="action"
value="learn">

<input type="hidden"
name="code"
value="{{ code }}">

<input type="text"
name="meaning"
placeholder="What does {{ code }} mean?"
required>

<button type="submit">
Save
</button>

</form>

</div>

{% endfor %}

{% endif %}


{% if saved %}

<p><strong>{{ saved }}</strong></p>

{% endif %}

</body>
</html>
"""
@app.route("/", methods=["GET", "POST"])
def home():

    result = ""
    unknowns = []
    saved = ""

    if request.method == "POST":

        action = request.form.get("action")

        if action == "learn":

            code = request.form.get("code", "").strip()
            meaning = request.form.get("meaning", "").strip()

            if code and meaning:
                save_code(code, meaning)
                saved = code + " = " + meaning + " saved."

        elif action == "read":

            photo = request.files.get("photo")

            if photo:

                try:
                    image = base64.b64encode(
                        photo.read()
                    ).decode("utf-8")

                    mime = photo.mimetype or "image/jpeg"

                    codes = load_codes()

                    code_text = "\n".join(
                        key + " = " + value
                        for key, value in codes.items()
                    )

                    prompt = """
Read this handwritten cafe order.

Known codes:
""" + code_text + """
IMPORTANT RULES:

The + sign and dots are SEPARATORS between different products.
NEVER combine the codes on either side of + into one product.

Read each code separately from left to right.

For example:
T + C = two separate products:
- T
- C

Because C is in the known codes, C must be Coffee.

If T is not in the known codes, try to interpret T from the
handwriting and cafe context. If you are confident T means Tea,
write Tea.

If you cannot confidently understand T, write:
UNKNOWN: T

NEVER interpret T + C as "Coffee (T + C)".
NEVER ignore one side of a + sign.

More examples:
C + B = Coffee AND Bacon.
BC + E = Black Coffee AND Egg.
C + L = Coffee AND Latte.
BC + C = Black Coffee AND Coffee.

2E means 2 Eggs.
3B means 3 Bacon.
L x2 means 2 Lattes.

Known codes always have priority over guesses.
Do not change the meaning of a known code.


A circled number is usually the table number.

Hope 1, Hope 2, Hope 3 and Hope 4
are set menus.

No S -> Bubble means remove
Sausage and replace it with Bubble.

Do not guess unknown shorthand.

If you see shorthand that is not
in the known codes, write:

UNKNOWN: followed by the code.

UNKNOWN RULES:

First try to understand an unknown handwritten code using the
cafe context.

If you are reasonably confident what it means, use the product
name normally and DO NOT put that code in UNKNOWN.

Example:
If T clearly appears to mean Tea, output Tea under DRINKS.
Do NOT also put T under UNKNOWN.

Only use UNKNOWN when you genuinely cannot determine what a code means.

VERY IMPORTANT:
UNKNOWN must contain ONLY individual raw handwritten codes.
Do not write explanations, sentences or comments in UNKNOWN.
Do not use brackets or descriptions.
Do not put known codes in UNKNOWN.
Do not put a whole expression such as F + S into UNKNOWN.
Separate unknown codes individually.

For example, if you cannot understand T, F0 and ON, output exactly:

UNKNOWN:
T
F0
ON

If there are no genuinely unknown codes, output exactly:

UNKNOWN:
None

Return the order using exactly this structure:

TABLE:
DRINKS:
SET MENU:
ITEMS:
CHANGES:
UNKNOWN:
"""

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
                                        "data:" + mime +
                                        ";base64," + image
                                    }
                                ]
                            }
                        ]
                    )

                    result = response.output_text

                    for line in result.splitlines():

                        if line.upper().startswith("UNKNOWN:"):

                            value = line.split(
                                ":", 1
                            )[1].strip()

                            if value and value.lower() not in [
                                "none",
                                "n/a",
                                "unknown"
                            ]:

                                for code in value.split(","):

                                    code = code.strip()

                                    if code and code not in unknowns:
                                        unknowns.append(code)
                except Exception as e:

                    result = "ERROR: " + str(e)

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
