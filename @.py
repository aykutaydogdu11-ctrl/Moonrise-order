from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64

app = Flask(__name__)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Moonrise Order App</title>
</head>

<body style="font-family: Arial; padding: 20px; max-width: 700px; margin: auto;">

    <h1>Moonrise Order App</h1>
    <h2>Order Photo</h2>

    <form method="POST" enctype="multipart/form-data">

        <input
            type="file"
            name="photo"
            accept="image/*"
            capture="environment"
            required
        >

        <br><br>

        <button type="submit" style="padding:12px 20px;">
            Read Order
        </button>

    </form>

    {% if result %}

        <hr>

        <h2>Order Read</h2>

        <div style="
            white-space: pre-wrap;
            background: #f2f2f2;
            padding: 15px;
            border-radius: 10px;
            font-size: 18px;
        ">{{ result }}</div>

    {% endif %}

</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home():

    result = ""

    if request.method == "POST":

        photo = request.files.get("photo")

        if photo:

            try:

                image_bytes = photo.read()

                image_base64 = base64.b64encode(
                    image_bytes
                ).decode("utf-8")

                mime_type = photo.mimetype or "image/jpeg"

                response = client.responses.create(

                    model="gpt-5.6-luna",

                    input=[
                        {
                            "role": "user",
                            "content": [

                                {
                                    "type": "input_text",
                                    "text": """
You are reading a handwritten order slip from Moonrise / Hope Cafe.

Read the handwriting
