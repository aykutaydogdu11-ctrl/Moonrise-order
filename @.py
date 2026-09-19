from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64

app = Flask(__name__)

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width, initial-scale=1">
<title>Moonrise Order App</title>
</head>

<body style="font-family:Arial;padding:20px">

<h1>Moonrise Order App</h1>
<h2>Order Photo</h2>

<form method="POST"
enctype="multipart/form-data">

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
<h2>Order Read</h2>

<div style="white-space:pre-wrap;
background:#eee;
padding:15px">

{{ result }}

</div>

{% endif %}

</body>
</html>
"""

PROMPT = """
Read this handwritten cafe order slip.

Codes:
C = White Coffee
BC = Black Coffee
L = Latte
Can = Can drink
Bottle = Bottle drink
E = Egg
SE = Scrambled Egg
B = Bacon
S = Sausage
Bubble = Bubble

2E = 2 Eggs
3B = 3 Bacon
2S = 2 Sausages
L x2 = 2 Lattes

Dots separate products.

A circled number is normally
the table number.

Hope 1, Hope 2, Hope 3 and Hope 4
are set menus.

No S -> Bubble means remove
Sausage and replace it with Bubble.

Do not guess unreadable handwriting.

Return:
TABLE:
DRINKS:
SET MENU:
ITEMS:
CHANGES:
UNCERTAIN:
"""
@app.route("/", methods=["GET", "POST"])
def home():

    result = ""

    if request.method == "POST":

        photo = request.files.get("photo")

        if photo:

            try:

                image = base64.b64encode(
                    photo.read()
                ).decode("utf-8")

                mime = photo.mimetype or "image/jpeg"

                response = client.responses.create(
                    model="gpt-5.4-nano",
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": PROMPT
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

            except Exception as e:

                result = "ERROR: " + str(e)

    return render_template_string(
        PAGE,
        result=result
    )


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
