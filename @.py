from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

RULES_FILE = "learned_codes.json"

DEFAULT_CODES = {
    "C": "White Coffee",
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

A dot . is the ONLY separator between different products.
VERY IMPORTANT:

Never skip a product between dots.

Each section separated by a dot . represents one product.
The number of readable sections must match the number of products in the result.

Example:

C . L . Bottle

has 3 products and MUST produce all 3:
White Coffee
Latte
Bottle

Do not merge them.
Do not omit L.
Do not change C into BC.
C means White Coffee.
BC means Black Coffee.
L means Latte.

Example:

E . B . PE

has 3 products and MUST produce all 3:
Egg
Bacon
PE

If PE is unknown, keep PE in the item AND put PE in UNKNOWN.

Never drop E, B, C, L or any other clearly readable known code.

The + sign is NOT used as a separator.
Do NOT interpret + as separating products.

Read each product between dots separately from left to right.

Examples:

C . L
means:
Coffee
Latte

T . C
means:
Tea
Coffee

BC . E
means:
Black Coffee
Egg

B . S
means:
Bacon
Sausage

2E means 2 Eggs.
3B means 3 Bacon.
L x2 means 2 Lattes.

Numbers normally indicate quantity.
A number is NOT an unknown product code.

A dot . separates different products.
Spaces can be part of one complete food instruction.

For example:

SE ON 2 TST (BROWN)

is ONE food instruction and means:

Scrambled Egg on 2 Brown Toast

SE = Scrambled Egg
ON = the word "on", not a product
TST = Toast
BROWN = Brown Toast

Never interpret any letter in TST as a separator.

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

ORDER PHRASE RULES:

Some handwritten codes combine to form one complete food order.
Do not automatically treat every word or code as a separate product.

The dot . is the ONLY separator between different products.

Example:
B . S . E

means three separate products:
Bacon
Sausage
Egg

A space does NOT separate products.

Some codes and words together form one complete food instruction.

Example:
SE ON 2 TST (BROWN)

must be interpreted as:
Scrambled Egg on 2 Brown Toast

In this pattern:
SE means Scrambled Egg.
ON connects the food to the toast.
2 means quantity 2.
TST means Toast.
BROWN means Brown Toast.

Do NOT put ON, 2, TST or BROWN in UNKNOWN when they are used
in this pattern.

Numbers such as 1, 2 and 3 usually indicate quantity.
A number by itself is NOT an unknown product code.

Never mistake handwritten T or any letter in TST for a separator.

Always read the complete handwritten food phrase before deciding
that individual parts are UNKNOWN.
UNKNOWN RULES:
Never silently ignore handwritten text.

Every readable order line on the paper must appear somewhere in the result.

First check whether a code exists in the KNOWN CODES.
If a code is known, use its saved meaning.

If a short product code is NOT in KNOWN CODES, do NOT invent its meaning.
Keep the exact code in the order and also put it in UNKNOWN.

Example:

E . B . PE

If E and B are known but PE is not known, output:

- Egg
- Bacon
- PE

UNKNOWN:
PE

Unknown codes must NEVER cause the rest of the line to disappear.

If one part of a line is unknown, still process all known parts of that line.

UNKNOWN must contain only the exact handwritten unknown code.
Do not put explanations, quantities, connector words or complete food
instructions in UNKNOWN.

Numbers are quantities, not unknown codes.
ON is a connector word, not an unknown product.

If there are no unknown product codes, output:

UNKNOWN:
None

Return the order using exactly this structure:

DRINKS:
List ONLY drinks here.
Write each drink on a separate line.
Convert known drink codes to their product names.

ITEMS:
Number each separate food order starting from 1.
Set menus such as Hope 1, Hope 2, Hope 3 and Hope 4 are also ITEMS.
Do not put set menus in a separate SET MENU section.

Any change written directly under a food or set menu belongs to that item.

Example:

Hope 1
No E -> S

must be shown as:

2- Hope 1
   No E -> S

Do not move No E -> S to another item.
Keep related instructions underneath the food they belong to.

Example:

1- Scrambled Egg on 2 Toast

2- Hope 1
   No E -> S

3- Egg
   Bacon
   PE

Do NOT silently remove any handwritten food line.

TABLE:
Write the table number here.

UNKNOWN:
Write each unknown product code here.
If there are no unknown codes, write None.
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

                    lines = result.splitlines()
    
                    for i, line in enumerate(lines):
                        stripped = line.strip()
    
                if stripped.upper() == "UNKNOWN:":
                    for next_line in lines[i + 1:]:
                        code = next_line.strip()
    
                        if not code:
                            continue
    
                        if code.upper().endswith(":"):
                            break
    
                        if code.lower() in ["none", "n/a", "unknown"]:
                            break
    
                        if code not in unknowns:
                            unknowns.append(code)
    
                elif stripped.upper().startswith("UNKNOWN:"):
                    value = stripped.split(":", 1)[1].strip()
    
                    if value and value.lower() not in ["none", "n/a", "unknown"]:
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
