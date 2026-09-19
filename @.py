from flask import Flask, request, render_template_string

app = Flask(__name__)

PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Moonrise Order App</title>
</head>

<body style="font-family: Arial; padding: 20px;">

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

        <button type="submit">
            Read Order
        </button>

    </form>

    {% if message %}
        <h3>{{ message }}</h3>
    {% endif %}

</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def home():

    message = ""

    if request.method == "POST":

        photo = request.files.get("photo")

        if photo:
            message = "Photo received successfully!"

    return render_template_string(
        PAGE,
        message=message
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
